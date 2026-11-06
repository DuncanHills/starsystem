"""Persistent record of which songs have already been synced.

We record the IDs of songs we have synced. A song is downloaded at most once, no
matter when it was starred, and moving or deleting the local copy won't cause it to come back.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATE_FILE_NAME = ".starsystem.json"
STATE_VERSION = 1


class StateError(Exception):
    pass


@dataclass
class SyncedSong:
    path: str
    starred: str | None
    synced_at: str

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "starred": self.starred, "synced_at": self.synced_at}


@dataclass
class SyncState:
    """Synced songs, keyed by server then song ID (song IDs are only unique per server)."""

    path: Path
    servers: dict[str, dict[str, SyncedSong]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> SyncState:
        if not path.exists():
            return cls(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise StateError(f"Could not read state file {path}: {e}") from e
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            raise StateError(f"Unrecognized state file format in {path}")
        servers = {
            server: {
                song_id: SyncedSong(
                    path=entry.get("path", ""),
                    starred=entry.get("starred"),
                    synced_at=entry.get("synced_at", ""),
                )
                for song_id, entry in songs.items()
            }
            for server, songs in (raw.get("servers") or {}).items()
        }
        return cls(path, servers)

    def save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "servers": {
                server: {song_id: song.to_json() for song_id, song in sorted(songs.items())}
                for server, songs in sorted(self.servers.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".starsystem-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def songs_for(self, server: str) -> dict[str, SyncedSong]:
        return self.servers.setdefault(server, {})

    def is_synced(self, server: str, song_id: str) -> bool:
        return song_id in self.servers.get(server, {})

    def mark_synced(self, server: str, song_id: str, path: str, starred: str | None) -> None:
        self.songs_for(server)[song_id] = SyncedSong(
            path=path, starred=starred, synced_at=datetime.now(UTC).isoformat(timespec="seconds")
        )
