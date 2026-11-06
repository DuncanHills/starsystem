"""The sync algorithm, kept separate from the CLI so it can be tested directly."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from starsystem.client import ClientError, SubsonicClient, SubsonicError
from starsystem.state import SyncState

log = logging.getLogger(__name__)

EPOCH = datetime.fromtimestamp(0, UTC)
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")
_ON_WINDOWS = os.name == "nt"
# Only characters the local filesystem can't store are replaced, so paths match files that
# older versions of starsystem (which used the server path verbatim) already downloaded.
_UNSAFE_CHARS_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]' if _ON_WINDOWS else r"[\x00/]")

# Some file servers (macOS SMB, Samba's catia/fruit modules, Windows "Services for Macintosh")
# store characters that NTFS doesn't allow as Unicode private-use characters, and music servers
# reading those shares report them that way. Map them back to the real characters.
_SFM_DECODE = {0xF000 + c: c for c in range(0x01, 0x20)} | {
    0xF020: '"',
    0xF021: "*",
    0xF022: ":",
    0xF023: "<",
    0xF024: ">",
    0xF025: "?",
    0xF026: "\\",
    0xF027: "|",
    0xF028: " ",  # trailing space
    0xF029: ".",  # trailing period
}


@dataclass
class SyncResult:
    downloaded: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)  # already on disk, now tracked
    skipped: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def parse_starred(value: str | None) -> datetime | None:
    """Parse a Subsonic `starred` timestamp (ISO 8601, usually UTC with a Z suffix)."""
    if not value:
        return None
    try:
        # Some servers send nanoseconds; datetime only handles microseconds on older Pythons.
        parsed = datetime.fromisoformat(_FRACTION_RE.sub(r"\1", value.strip()))
    except ValueError:
        log.debug("Could not parse starred date %r", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_audio(song: dict[str, Any]) -> bool:
    if song.get("isVideo"):
        return False
    content_type = song.get("contentType")
    return content_type is None or content_type.startswith("audio")


def _clean_component(part: str) -> str:
    part = _UNSAFE_CHARS_RE.sub("_", part)
    if _ON_WINDOWS:  # Windows silently drops trailing dots and spaces
        part = part.rstrip(". ")
    return part if part.strip() else "_"


def song_relative_path(song: dict[str, Any]) -> PurePosixPath:
    """Where a song should live relative to the download directory.

    Uses the server-reported `path` when present. Absolute paths and `..` are removed so a
    malicious or odd server can't write outside the download directory; otherwise the path is
    kept as-is, except for characters the local filesystem can't store.
    Falls back to Artist/Album/NN - Title.ext.
    """
    raw = song.get("path")
    if raw:
        raw = str(raw).translate(_SFM_DECODE)
        if _ON_WINDOWS:
            raw = str(raw).replace("\\", "/")
        parts = [p for p in PurePosixPath(str(raw)).parts if p not in ("/", "")]
        parts = [_clean_component(p) for p in parts if p not in (".", "..")]
        if parts:
            return PurePosixPath(*parts)

    artist = song.get("artist") or song.get("displayArtist") or "Unknown Artist"
    album = song.get("album") or "Unknown Album"
    title = song.get("title") or str(song.get("id", "unknown"))
    track = song.get("track")
    suffix = song.get("suffix") or "mp3"
    name = f"{int(track):02d} - {title}" if isinstance(track, int) else title
    return PurePosixPath(
        _clean_component(artist), _clean_component(album), _clean_component(f"{name}.{suffix}")
    )


def download_to(client: SubsonicClient, song_id: str, dest: Path) -> None:
    """Stream a song into a temp file next to `dest`, then move it into place.

    Never clobbers: if something appears at `dest` while we download, it wins.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".starsystem-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in client.download(song_id):
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        if dest.exists():
            log.warning("%s appeared during download; leaving it alone", dest)
            return
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def sync_starred(
    client: SubsonicClient,
    download_path: Path,
    state: SyncState,
    *,
    since: datetime | None = None,
    dry_run: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> SyncResult:
    """Download starred songs that haven't been synced yet.

    Without `since`: every starred song not yet recorded in the state file is downloaded.
    With `since`: every song starred at or after `since` is downloaded if it is missing from
    disk, even if it was synced before (use this to restore files you deleted). Songs starred
    earlier than `since` are not downloaded, and are recorded as synced so that later runs
    without `since` skip them too. That makes `since` a baseline for a fresh sync directory.

    Existing files are never overwritten; a song already on disk is simply recorded as synced.
    """
    progress = on_progress or (lambda _msg: None)
    server = client.base_url
    result = SyncResult()

    songs = [s for s in client.get_starred_songs() if is_audio(s) and s.get("id") is not None]
    songs.sort(key=lambda s: parse_starred(s.get("starred")) or EPOCH)

    for song in songs:
        song_id = str(song["id"])
        rel = song_relative_path(song)
        dest = download_path / rel
        starred_raw = song.get("starred")
        starred = parse_starred(starred_raw)

        if since is not None:
            if starred is not None and starred < since:
                if not dry_run and not state.is_synced(server, song_id):
                    state.mark_synced(server, song_id, str(rel), starred_raw)
                result.skipped += 1
                continue
        elif state.is_synced(server, song_id):
            result.skipped += 1
            continue

        if dest.exists():
            if not state.is_synced(server, song_id):
                result.adopted.append(str(rel))
                if not dry_run:
                    state.mark_synced(server, song_id, str(rel), starred_raw)
            else:
                result.skipped += 1
            continue

        if dry_run:
            progress(f"Would download {rel}")
            result.downloaded.append(str(rel))
            continue

        progress(f"Downloading {rel}")
        try:
            download_to(client, song_id, dest)
        except (ClientError, SubsonicError, OSError) as e:
            log.error("Failed to download %s: %s", rel, e)
            result.failed.append((str(rel), str(e)))
            continue
        result.downloaded.append(str(rel))
        state.mark_synced(server, song_id, str(rel), starred_raw)
        # Save after every song so an interrupted run doesn't lose progress.
        state.save()

    if not dry_run:
        state.save()
    return result
