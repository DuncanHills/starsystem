# starsystem
A python tool for syncing your starred songs from a Subsonic server to a local directory.

A best-attempt is made to track the history of synced files so they are not re-synced if you move or delete them. If you provide the `--since` option, all files since that date will be synced. Starsystem will not clobber existing files, so feel free to retag your local copies.

I use starsystem in combination with file sync software (SyncThing) to bridge the gap between Subsonic and Rekordbox on multiple machines. You should too!

## Download

If you want to download a pre-built copy, you can find the latest release [here](https://github.com/ascrane/starsystem/releases/latest).

## Build

First, clone the repo and build a copy of starsystem:
```
git clone https://github.com/ascrane/starsystem.git
cd starsystem
./pants package :
```

You can now run the [PEX](https://pex.readthedocs.org/en/stable/). Copy it somewhere useful (like a location included in your path) or schedule it in your crontab/[launchd](https://developer.apple.com/library/mac/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html).
```
./dist/starsystem.pex --help
```

## Usage

You will need an API token to use this script. You can generate it by following the [Subsonic API documentation](http://www.subsonic.org/pages/api.jsp) or running starsystem with the `token` 
command.

### Commands
```
Usage: starsystem.pex [OPTIONS] COMMAND [ARGS]...

  A python tool for syncing your starred songs from a Subsonic server to a
  local directory.

  Run `sync --help` for more detailed options and usage information.

Options:
  --help  Show this message and exit.

Commands:
  sync   Sync your starred items in subsonic to the specified directory.
  token  Generate an API token interactively.

$ starsystem.pex sync --help
```

### Sync Command
The meat and potatoes of the application.
```
Usage: starsystem.pex sync [OPTIONS]

  Sync your starred items in subsonic to the specified directory.

  A best-attempt is made to track the history of synced files so they are not
  re-synced if you  move or delete them. If you provide the --since option,
  all files since that date will be  synced. Starsystem will not clobber
  existing files, so feel free to retag your local copies.

Options:
  -i, --uri TEXT                  URI of the Subsonic server.  [required]
  -u, --user TEXT                 Username on the specified Subsonic server.
                                  [required]
  -t, --token TEXT                API token for the given username/salt
                                  combination. See:
                                  http://www.subsonic.org/pages/api.jsp
                                  [required]
  -s, --salt TEXT                 Salt used to generate the API token.
                                  [required]
  -p, --path PATH                 Path of the directory whither songs will be
                                  downloaded.  [required]
  -S, --since [%Y-%m-%d|%Y-%m-%dT%H:%M:%S|%Y-%m-%d %H:%M:%S]
                                  Collect all songs since the specified date.
  -I, --insecure / --no-insecure  Don't verify SSL certificates. Verification
                                  is enabled by default.
  -v, --debug / --no-debug        Enable debug output.
  --help                          Show this message and exit.  
```
### Token Command
A small utility to help you generate your API token in case you forget how.
```
$ starsystem.pex token --help
Usage: starsystem.pex token [OPTIONS]

  Generate an API token interactively.

Options:
  --help  Show this message and exit.
```