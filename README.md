# starsystem

A Python tool for syncing your starred songs from a Subsonic or [OpenSubsonic](https://opensubsonic.netlify.app/) server (Navidrome, Gonic, Airsonic-Advanced, …) to a local directory.

Each starred song is downloaded **once**. starsystem records the IDs of the songs it has synced, so moving, renaming, retagging or deleting your local copies won't make them come back. Existing files are never overwritten. If you want to restore songs you deleted, use `--since`.

I use starsystem with file sync software (Syncthing) to bridge the gap between Subsonic and Rekordbox on multiple machines. You should too!

## Install

starsystem needs Python 3.11 or newer. The easiest way to install it is with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/DuncanHills/starsystem.git
```

Or run it once without installing anything:

```bash
uvx --from git+https://github.com/DuncanHills/starsystem.git starsystem --help
```

## Development

```bash
git clone https://github.com/DuncanHills/starsystem.git
cd starsystem
uv sync                 # create .venv with all dependencies
uv run starsystem --help
uv run pytest           # tests
uv run ruff check .     # lint
uv build                # wheel + sdist in dist/
```

### Single-file executable (PEX)

To build a self-contained `dist/starsystem.pex` that runs anywhere Python 3.11+ is installed, with dependencies pinned to `uv.lock`, run:

```bash
./scripts/build-pex.sh
```

Copy it somewhere on your `PATH`, or schedule it with cron or [launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html):

```bash
./dist/starsystem.pex --help
```

## Authentication

Use one of these, from most to least preferred:

1. **API key** (`--api-key`): requires an OpenSubsonic server with the `apiKeyAuthentication` extension. Generate a key in your server's web UI.
2. **Username + password** (`--user` and `--password`): starsystem generates a fresh salted token for every request, so your password is never sent over the wire.
3. **Legacy token** (`--user`, `--token` and `--salt`): a pre-computed `md5(password + salt)`. `starsystem token` will generate one for you.

Every option can be set with an environment variable instead (`STARSYSTEM_URL`, `STARSYSTEM_API_KEY`, `STARSYSTEM_USER`, `STARSYSTEM_PASSWORD`, `STARSYSTEM_TOKEN`, `STARSYSTEM_SALT`, `STARSYSTEM_PATH`). That keeps secrets out of your shell history, `ps` output, and crontab.

Run `starsystem check` to confirm that the server is reachable and your credentials work:

```bash
STARSYSTEM_API_KEY=... starsystem check --url https://music.example.com
```

When the server supports the OpenSubsonic `formPost` extension, credentials are sent in the request body rather than in the URL, which keeps them out of server and proxy logs.

## Usage

```bash
export STARSYSTEM_URL=https://music.example.com
export STARSYSTEM_API_KEY=...
starsystem sync --path ~/Music/Starred
```

### Options

| Option | Description |
| --- | --- |
| `-i, --url` | Base URL of the server (`/rest` is optional). |
| `-k, --api-key` | OpenSubsonic API key. |
| `-u, --user`, `-P, --password` | Username and password. |
| `-t, --token`, `-s, --salt` | Legacy pre-computed token and its salt. |
| `-p, --path` | Directory that songs are downloaded into. |
| `-S, --since DATE` | Ignore sync history and re-download every song starred at or after `DATE` (local time) that is missing from disk. Earlier songs are skipped, on this run and on later ones. Accepts `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SS` or `YYYY-MM-DD HH:MM:SS`. |
| `--state-file` | Where sync history is stored. Defaults to `<path>/.starsystem.json`. |
| `-n, --dry-run` | Show what would be downloaded, and change nothing. |
| `-I, --insecure` | Don't verify TLS certificates. |
| `--timeout` | Network timeout in seconds (default 60). |
| `-v, --debug` | Verbose logging. |

A song that fails to download is logged and skipped, and the rest of the sync continues. starsystem exits with status 1 if any song failed. The state file is saved after every download, so an interrupted run loses nothing.

### How sync history works

`.starsystem.json` maps each server to the IDs of the songs synced from it, along with their paths and starred dates. On each run:

- **Without `--since`**: every starred song whose ID isn't in the history is downloaded. Songs you star later are always picked up, even if the server reports an older starred date.
- **With `--since`**: songs starred on or after that date are downloaded if they're missing from disk, whatever the history says. Songs starred before that date are not downloaded, and are recorded as synced so that later runs skip them too.
- In both cases, if a file already exists at a song's path it is kept and recorded as synced.

### Upgrading from starsystem 1.x

The `.synced_to` file from 1.x is no longer read, and you can delete it. On its first run, 2.x records every song that is already on disk as synced and downloads any starred songs that are missing. If you've deleted songs you don't want back, run once with `--since` set to today to start tracking without downloading the backlog.

`--uri` is now `--url`, and `--token`/`--salt` can be replaced with `--password` or `--api-key`.
