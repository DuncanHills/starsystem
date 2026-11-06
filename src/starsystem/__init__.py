"""Sync your starred songs from a Subsonic/OpenSubsonic server to a local directory."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("starsystem")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0+unknown"
