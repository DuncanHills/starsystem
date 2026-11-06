import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from click.testing import CliRunner

from starsystem.cli import cli
from starsystem.client import (
    ApiKeyAuth,
    ClientError,
    PasswordAuth,
    SubsonicClient,
    SubsonicError,
    make_token,
)
from starsystem.state import STATE_FILE_NAME, SyncState
from starsystem.sync import parse_starred, song_relative_path, sync_starred

BASE = "https://music.example.com"


def envelope(**body):
    return {"subsonic-response": {"status": "ok", "version": "1.16.1", **body}}


def song(id, path, starred, **extra):
    return {"id": id, "path": path, "starred": starred, "contentType": "audio/mpeg", **extra}


def mock_server(rsps, songs, extensions=("apiKeyAuthentication",)):
    rsps.get(
        f"{BASE}/rest/getOpenSubsonicExtensions.view",
        json=envelope(
            openSubsonic=True,
            type="navidrome",
            serverVersion="0.60",
            openSubsonicExtensions=[{"name": n, "versions": [1]} for n in extensions],
        ),
    )
    rsps.get(f"{BASE}/rest/ping.view", json=envelope())
    rsps.get(f"{BASE}/rest/getStarred2.view", json=envelope(starred2={"song": songs}))

    def download(request):
        params = parse_qs(urlparse(request.url).query)
        return (200, {"Content-Type": "audio/mpeg"}, f"audio-{params['id'][0]}".encode())

    rsps.add_callback(responses.GET, f"{BASE}/rest/download.view", callback=download)


def make_client():
    client = SubsonicClient(BASE, ApiKeyAuth("secret"))
    client.check_auth()
    return client


SONGS = [
    song("1", "A/Album/01 - One.mp3", "2024-01-01T10:00:00Z"),
    song("2", "B/Album/02 - Two.mp3", "2024-06-01T10:00:00.123Z"),
    song("3", "C/Album/03 - Three.mp3", "2025-01-01T10:00:00.112827504Z"),
    {"id": "v", "path": "video.mp4", "starred": "2025-01-01T00:00:00Z", "isVideo": True},
]


# -- client -------------------------------------------------------------------


@responses.activate
def test_api_key_auth_sends_only_api_key():
    mock_server(responses, SONGS)
    make_client().get_starred_songs()
    query = parse_qs(urlparse(responses.calls[-1].request.url).query)
    assert query["apiKey"] == ["secret"]
    assert not {"u", "p", "t", "s"} & query.keys()
    assert query["c"] == ["starsystem"] and query["f"] == ["json"]


@responses.activate
def test_api_key_requires_server_support():
    mock_server(responses, SONGS, extensions=())
    with pytest.raises(ClientError, match="apiKeyAuthentication"):
        make_client()


@responses.activate
def test_form_post_used_when_supported():
    mock_server(responses, SONGS, extensions=("apiKeyAuthentication", "formPost"))
    responses.post(f"{BASE}/rest/ping.view", json=envelope())
    make_client()
    call = responses.calls[-1].request
    assert call.method == "POST"
    assert "apiKey=secret" in call.body
    assert "apiKey" not in call.url


@responses.activate
def test_subsonic_error_is_raised():
    responses.get(
        f"{BASE}/rest/getOpenSubsonicExtensions.view", json=envelope(openSubsonicExtensions=[])
    )
    responses.get(
        f"{BASE}/rest/ping.view",
        json={
            "subsonic-response": {
                "status": "failed",
                "error": {"code": 40, "message": "Wrong username or password"},
            }
        },
    )
    client = SubsonicClient(BASE, PasswordAuth("joe", "pw"))
    with pytest.raises(SubsonicError) as exc:
        client.check_auth()
    assert exc.value.code == 40


def test_password_auth_salts_every_request():
    auth = PasswordAuth("joe", "sesame")
    a, b = auth.params(), auth.params()
    assert a["s"] != b["s"]
    assert a["t"] == make_token("sesame", a["s"])
    assert make_token("sesame", "c19b2d") == "26719a1196d2a940705a59634eb18eab"  # from the spec


def test_base_url_normalization():
    assert SubsonicClient("music.example.com/rest/", ApiKeyAuth("k")).base_url == BASE


# -- helpers ------------------------------------------------------------------


def test_parse_starred_variants():
    assert parse_starred("2023-03-27T09:45:27Z") == datetime(2023, 3, 27, 9, 45, 27, tzinfo=UTC)
    assert parse_starred("2023-03-14T17:51:22.112827504Z").microsecond == 112827
    assert parse_starred("2023-03-27T09:45:27").tzinfo == UTC
    assert parse_starred("garbage") is None
    assert parse_starred(None) is None


def test_song_path_is_sanitized():
    assert str(song_relative_path({"path": "../../etc/passwd"})) == "etc/passwd"
    assert str(song_relative_path({"path": "/abs/a.mp3"})) == "abs/a.mp3"
    # Names that old versions wrote verbatim must map to the same file.
    for name in ['A/B: "C"?.mp3', "Album /01 - x.flac", "A/ 10 - y.flac", "A/one\ntwo.flac"]:
        assert str(song_relative_path({"path": name})) == name
    # Private-use characters from SMB/NTFS shares are decoded to the characters they stand for.
    reported = "Gunter \uf020Ruit\uf020 Kraus [FLAC]\uf028/01 - Bang\uf025 x\uf00ay.flac"
    assert (
        str(song_relative_path({"path": reported}))
        == 'Gunter "Ruit" Kraus [FLAC] /01 - Bang? x\ny.flac'
    )
    fallback = {
        "id": "9",
        "artist": "AC/DC",
        "album": "X",
        "title": "T",
        "track": 3,
        "suffix": "flac",
    }
    assert str(song_relative_path(fallback)) == "AC_DC/X/03 - T.flac"


# -- sync ---------------------------------------------------------------------


@responses.activate
def test_first_sync_downloads_everything_and_records_state(tmp_path: Path):
    mock_server(responses, SONGS)
    state = SyncState.load(tmp_path / STATE_FILE_NAME)
    result = sync_starred(make_client(), tmp_path, state)

    assert sorted(result.downloaded) == sorted(s["path"] for s in SONGS[:3])
    assert (tmp_path / "A/Album/01 - One.mp3").read_bytes() == b"audio-1"
    assert not (tmp_path / "video.mp4").exists()
    saved = json.loads((tmp_path / STATE_FILE_NAME).read_text())
    assert set(saved["servers"][BASE]) == {"1", "2", "3"}
    assert not list(tmp_path.rglob("*.part"))


@responses.activate
def test_deleted_files_are_not_redownloaded(tmp_path: Path):
    mock_server(responses, SONGS)
    sync_starred(make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME))
    (tmp_path / "A/Album/01 - One.mp3").unlink()

    result = sync_starred(make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME))
    assert result.downloaded == []
    assert not (tmp_path / "A/Album/01 - One.mp3").exists()


@responses.activate
def test_newly_starred_old_song_is_still_synced(tmp_path: Path):
    """The old high-water-mark approach missed songs whose starred date was older."""
    mock_server(responses, SONGS[1:3])
    sync_starred(make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME))

    responses.reset()
    mock_server(responses, SONGS)  # song 1 has the oldest date but appears later
    result = sync_starred(make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME))
    assert result.downloaded == ["A/Album/01 - One.mp3"]


@responses.activate
def test_since_restores_deleted_files_after_date_only(tmp_path: Path):
    mock_server(responses, SONGS)
    sync_starred(make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME))
    for s in SONGS[:3]:
        (tmp_path / s["path"]).unlink()

    since = datetime(2024, 5, 1, tzinfo=UTC)
    state = SyncState.load(tmp_path / STATE_FILE_NAME)
    result = sync_starred(make_client(), tmp_path, state, since=since)
    assert sorted(result.downloaded) == ["B/Album/02 - Two.mp3", "C/Album/03 - Three.mp3"]
    assert not (tmp_path / "A/Album/01 - One.mp3").exists()


@responses.activate
def test_existing_files_are_never_clobbered(tmp_path: Path):
    mock_server(responses, SONGS)
    existing = tmp_path / "A/Album/01 - One.mp3"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"my retagged copy")

    result = sync_starred(make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME))
    assert existing.read_bytes() == b"my retagged copy"
    assert result.adopted == ["A/Album/01 - One.mp3"]


@responses.activate
def test_dry_run_writes_nothing(tmp_path: Path):
    mock_server(responses, SONGS)
    result = sync_starred(
        make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME), dry_run=True
    )
    assert len(result.downloaded) == 3
    assert list(tmp_path.iterdir()) == []


@responses.activate
def test_failed_download_does_not_stop_others(tmp_path: Path):
    mock_server(responses, SONGS)
    responses.replace(
        responses.GET,
        f"{BASE}/rest/download.view",
        json={"subsonic-response": {"status": "failed", "error": {"code": 70, "message": "nope"}}},
    )
    state = SyncState.load(tmp_path / STATE_FILE_NAME)
    result = sync_starred(make_client(), tmp_path, state)
    assert len(result.failed) == 3
    assert not state.is_synced(BASE, "1")
    assert not list(tmp_path.rglob("*.mp3"))


# -- CLI ----------------------------------------------------------------------


@responses.activate
def test_cli_sync(tmp_path: Path):
    mock_server(responses, SONGS)
    result = CliRunner().invoke(
        cli,
        ["sync", "--url", BASE, "--path", str(tmp_path)],
        env={"STARSYSTEM_API_KEY": "secret"},
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "C/Album/03 - Three.mp3").exists()


def test_cli_rejects_conflicting_auth(tmp_path: Path):
    result = CliRunner().invoke(
        cli, ["sync", "-i", BASE, "-p", str(tmp_path), "-k", "key", "-u", "joe"]
    )
    assert result.exit_code == 2
    assert "cannot be combined" in result.output


def test_unreachable_server_reports_connection_error():
    client = SubsonicClient("http://127.0.0.1:9", ApiKeyAuth("k"), timeout=2)
    with pytest.raises(ClientError, match="Could not reach"):
        client.check_auth()


@responses.activate
def test_since_sets_baseline_for_later_runs(tmp_path: Path):
    mock_server(responses, SONGS)
    since = datetime(2024, 5, 1, tzinfo=UTC)
    first = sync_starred(
        make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME), since=since
    )
    assert "A/Album/01 - One.mp3" not in first.downloaded

    later = sync_starred(make_client(), tmp_path, SyncState.load(tmp_path / STATE_FILE_NAME))
    assert later.downloaded == []
    assert not (tmp_path / "A/Album/01 - One.mp3").exists()
