# starsystem
A python tool for syncing your starred songs from a Subsonic or [OpenSubsonic](https://opensubsonic.netlify.app/) server (Navidrome, Gonic, etc.) to a local directory.

Every starred song gets downloaded once. Starsystem remembers which songs it has synced, so if you move, rename, retag or delete your local copies, they won't come back. It will never clobber existing files either. If you do want deleted songs back, use `--since`.

I use starsystem in combination with file sync software (Syncthing) to bridge the gap between Subsonic and Rekordbox on multiple machines. You should too!

## Install

You'll need Python 3.11 or newer. The easiest way to get starsystem is with [uv](https://docs.astral.sh/uv/):
```
uv tool install git+https://github.com/DuncanHills/starsystem.git
```

## Build

If you'd rather have a single executable file, clone the repo and build a [PEX](https://docs.pex-tool.org/):
```
git clone https://github.com/DuncanHills/starsystem.git
cd starsystem
./scripts/build-pex.sh
```

This puts a `starsystem` executable in `dist/`, with dependencies pinned to whatever's in `uv.lock`. It runs anywhere Python 3.11+ is installed. Copy it somewhere useful (like a location in your path) or schedule it in your crontab/[launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html).
```
./dist/starsystem --help
```

If you want to hack on it:
```
uv sync
uv run starsystem --help
uv run pytest
uv run ruff check .
```

## Authentication

If your server supports OpenSubsonic API keys, use one. Generate a key in your server's web UI and pass it with `--api-key`.

Otherwise, pass `--user` and `--password`. Starsystem salts and hashes your password fresh for every request, so the password itself never gets sent. If you're still using a token and salt from an older version, `--token` and `--salt` still work, and the `token` command will make a token from your password and a salt in case you forget how.

Every option can also be set as an environment variable (`STARSYSTEM_URL`, `STARSYSTEM_API_KEY`, `STARSYSTEM_USER`, `STARSYSTEM_PASSWORD`, `STARSYSTEM_PATH`, etc.). I'd recommend that for anything secret, so your key doesn't end up in your shell history or crontab.

## Usage

```
Usage: starsystem [OPTIONS] COMMAND [ARGS]...

Commands:
  check  Check that the server is reachable and the credentials work.
  sync   Sync your starred songs to the specified directory.
  token  Generate a legacy salted token interactively.
```

### Sync Command
The meat and potatoes of the application.
```
export STARSYSTEM_URL=https://music.example.com
export STARSYSTEM_API_KEY=...
starsystem sync --path ~/Music/starred
```

```
Options:
  -i, --url TEXT                  Base URL of the Subsonic/OpenSubsonic
                                  server, e.g. https://music.example.com.
                                  [required]
  -k, --api-key TEXT              OpenSubsonic API key (preferred). Generate
                                  one in your server's web UI.
  -u, --user TEXT                 Username, for password or legacy token auth.
  -P, --password TEXT             Password. A salted token is derived per
                                  request; the password is never sent.
  -t, --token TEXT                Legacy: pre-computed md5(password + salt).
                                  Requires --salt.
  -s, --salt TEXT                 Legacy: salt for --token.
  -I, --insecure / --no-insecure  Don't verify TLS certificates.
  --timeout FLOAT                 Network timeout in seconds.  [default: 60.0]
  -v, --debug / --no-debug        Enable debug output.
  -p, --path DIRECTORY            Directory songs are downloaded into.
                                  [required]
  -S, --since [%Y-%m-%d|%Y-%m-%dT%H:%M:%S|%Y-%m-%d %H:%M:%S]
                                  Ignore sync history: (re)download every song
                                  starred at or after this local date/time
                                  that is missing from disk. Earlier songs are
                                  skipped now and on later runs.
  --state-file FILE               Where to record synced songs. Defaults to
                                  <path>/.starsystem.json.
  -n, --dry-run                   Show what would be downloaded and exit.
  -h, --help                      Show this message and exit.
```

Starsystem keeps track of what it has synced in `.starsystem.json` in your download directory. It's a list of song IDs for each server, so a song you star today gets picked up no matter when the server thinks it was starred. Don't use different `--url`s for the same server (like `http://` one day and `https://` the next), or starsystem will think it's a new server.

If a download fails, starsystem logs it and keeps going, then exits with status 1 at the end. It saves its progress after every song, so you can kill it halfway through and pick up where you left off.

`--since` does two jobs. Use it to sync only the songs you starred after a certain date, like when you're setting up a new folder and don't want your whole back catalog. Or use it to get stuff back, since it downloads everything starred on or after that date that isn't on disk, even songs starsystem already synced once and you deleted. Either way, anything starred before that date gets marked as synced and left alone, now and in future runs.

### Check Command
Makes sure starsystem can reach your server and log in, without downloading anything. Handy when you're setting things up, or when your cron job stops working. You'll see something like this:
```
$ starsystem check --url https://music.example.com
Connected to https://music.example.com
Server: navidrome 0.58.0
API version: 1.16.1
Extensions: apiKeyAuthentication, formPost, songLyrics, transcodeOffset
Authentication OK.
```

### Token Command
A small utility that makes a token from your password and a salt you pick, for `--token`/`--salt`, in case you forget how. You probably want `--password` or `--api-key` instead.

## Using an existing music folder

If you point starsystem at a folder that already has some of your starred songs in it, it'll figure out what you have. Any song already sitting at the path the server reports for it gets kept and marked as synced. Anything missing gets downloaded. Try a dry run first to see what it's going to do:
```
starsystem sync --path ~/Music/starred --dry-run
```

The last line tells you how many songs it would download and how many it found already present. If that list is longer than you expect, the usual reason is that the files are at different paths from the ones the server reports (maybe you renamed or reorganized them), so starsystem doesn't recognize them. The dry run prints the path it expects for each song, so you can compare.

If the list has songs you deleted on purpose and don't want back, run it once with `--since` set to today:
```
starsystem sync --path ~/Music/starred --since 2026-11-06
```

That marks everything you starred before today as synced without downloading it. From then on, starsystem only grabs songs you star after that.

If your server's music lives on an SMB or NTFS share, file names with characters like `?`, `:` or `"` can come through from the server as invisible placeholder characters. Starsystem translates those back, so they'll still match your local files.
