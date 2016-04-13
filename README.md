# starsystem
A python script for syncing your starred songs from a Subsonic server to a local directory.

A best-attempt is made to track the history of synced files so they are not re-synced if you move or delete them. If you provide the `--since` option, all files since that date will be synced. Starsystem will not clobber existing files, so feel free to retag your local copies.

I use starsystem in combination with BTSync to bridge the gap between Subsonic and Traktor on multiple machines. You should too!

## Build

First, clone the repo and build a copy of starsystem:
```
git clone https://github.com/ascrane/starsystem.git
cd starsystem
./pants binary :starsystem
```

You can now run the [PEX](https://pex.readthedocs.org/en/stable/). Copy it somewhere useful (like a location included in your path) or schedule it in your crontab/[launchd](https://developer.apple.com/library/mac/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html).
```
./dist/starsystem.pex --help
```

## Usage

See the [Subsonic API documentation](https://www.google.com) for information on generating your API token.

```
Usage: starsystem [opts]
Options marked with * are required.

Options:
  -h, --help, --short-help
                        show this help message and exit.
  --long-help           show options from all registered modules, not just the
                        __main__ module.
  -i SUBSONIC_URI, --uri=SUBSONIC_URI
                        * URI of the Subsonic server.
  -u USERNAME, --user=USERNAME
                        * Username on the specified Subsonic server.
  -t TOKEN, --token=TOKEN
                        * API token for the given username/salt combination
                        See: http://www.subsonic.org/pages/api.jsp
  -s SALT, --salt=SALT  * Salt used to generate the API token.
  -p DOWNLOAD_PATH, --path=DOWNLOAD_PATH
                        * Path to the directory whither songs will be
                        downloaded.
  -S SINCE, --since=SINCE
                        Collect all songs since the specified date.
  -I, --insecure        Don't verify SSL certificates. Verification is enabled
                        by default. [default: False]
```