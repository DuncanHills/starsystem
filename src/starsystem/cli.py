from __future__ import annotations

import logging
import sys
from datetime import datetime
from getpass import getpass
from pathlib import Path

import click
import urllib3

from starsystem import __version__
from starsystem.client import (
    ApiKeyAuth,
    Auth,
    ClientError,
    PasswordAuth,
    SubsonicClient,
    SubsonicError,
    TokenAuth,
    make_token,
)
from starsystem.state import STATE_FILE_NAME, StateError, SyncState
from starsystem.sync import sync_starred

log = logging.getLogger("starsystem")


def _connection_options(f):
    """Options shared by every command that talks to a server."""
    options = [
        click.option(
            "-i",
            "--url",
            "url",
            required=True,
            envvar="STARSYSTEM_URL",
            help="Base URL of the Subsonic/OpenSubsonic server, e.g. https://music.example.com.",
        ),
        click.option(
            "-k",
            "--api-key",
            envvar="STARSYSTEM_API_KEY",
            help="OpenSubsonic API key (preferred). Generate one in your server's web UI.",
        ),
        click.option(
            "-u",
            "--user",
            "username",
            envvar="STARSYSTEM_USER",
            help="Username, for password or legacy token auth.",
        ),
        click.option(
            "-P",
            "--password",
            envvar="STARSYSTEM_PASSWORD",
            help="Password. A salted token is derived per request; the password is never sent.",
        ),
        click.option(
            "-t",
            "--token",
            envvar="STARSYSTEM_TOKEN",
            help="Legacy: pre-computed md5(password + salt). Requires --salt.",
        ),
        click.option("-s", "--salt", envvar="STARSYSTEM_SALT", help="Legacy: salt for --token."),
        click.option(
            "-I",
            "--insecure/--no-insecure",
            default=False,
            help="Don't verify TLS certificates.",
        ),
        click.option(
            "--timeout",
            type=float,
            default=60.0,
            show_default=True,
            help="Network timeout in seconds.",
        ),
        click.option("-v", "--debug/--no-debug", default=False, help="Enable debug output."),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def _build_auth(api_key, username, password, token, salt) -> Auth:
    if api_key:
        if username or password or token or salt:
            raise click.UsageError(
                "--api-key cannot be combined with --user/--password/--token/--salt."
            )
        return ApiKeyAuth(api_key)
    if not username:
        raise click.UsageError("Provide --api-key, or --user with --password (or --token/--salt).")
    if password:
        return PasswordAuth(username, password)
    if token and salt:
        return TokenAuth(username, token, salt)
    if token or salt:
        raise click.UsageError("--token and --salt must be used together.")
    raise click.UsageError("Provide --password (or --token and --salt) along with --user.")


def _make_client(url, api_key, username, password, token, salt, insecure, timeout, debug):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)s: %(message)s" if not debug else "%(levelname)s %(name)s: %(message)s",
    )
    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    auth = _build_auth(api_key, username, password, token, salt)
    client = SubsonicClient(url, auth, verify_ssl=not insecure, timeout=timeout)
    try:
        client.check_auth()
    except (ClientError, SubsonicError) as e:
        raise click.ClickException(str(e)) from e
    return client


def _parse_since(value: datetime | None) -> datetime | None:
    # click.DateTime gives a naive datetime; interpret it in the local timezone.
    return value.astimezone() if value is not None else None


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="starsystem")
def cli() -> None:
    """Sync your starred songs from a Subsonic/OpenSubsonic server to a local directory.

    Run `starsystem sync --help` for options. Every option can also be given as an
    environment variable (STARSYSTEM_URL, STARSYSTEM_API_KEY, ...), which keeps secrets out of
    your shell history and process list.
    """


@cli.command()
@_connection_options
@click.option(
    "-p",
    "--path",
    "download_path",
    required=True,
    envvar="STARSYSTEM_PATH",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory songs are downloaded into.",
)
@click.option(
    "-S",
    "--since",
    type=click.DateTime(["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]),
    help="Ignore sync history: (re)download every song starred at or after this local "
    "date/time that is missing from disk. Earlier songs are skipped now and on later runs.",
)
@click.option(
    "--state-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help=f"Where to record synced songs. Defaults to <path>/{STATE_FILE_NAME}.",
)
@click.option("-n", "--dry-run", is_flag=True, help="Show what would be downloaded and exit.")
def sync(
    url,
    api_key,
    username,
    password,
    token,
    salt,
    insecure,
    timeout,
    debug,
    download_path: Path,
    since,
    state_file: Path | None,
    dry_run: bool,
) -> None:
    """Sync your starred songs to the specified directory.

    Each song is downloaded once: starsystem records the IDs of synced songs, so moving,
    renaming, retagging, or deleting your local copies won't cause them to be downloaded again.
    Existing files are never overwritten. Use --since to restore songs you've removed.
    """
    client = _make_client(url, api_key, username, password, token, salt, insecure, timeout, debug)
    download_path = download_path.expanduser()
    state_path = (state_file or download_path / STATE_FILE_NAME).expanduser()
    try:
        state = SyncState.load(state_path)
    except StateError as e:
        raise click.ClickException(str(e)) from e

    try:
        result = sync_starred(
            client,
            download_path,
            state,
            since=_parse_since(since),
            dry_run=dry_run,
            on_progress=log.info,
        )
    except (ClientError, SubsonicError) as e:
        raise click.ClickException(str(e)) from e

    verb = "Would download" if dry_run else "Downloaded"
    log.info(
        "%s %d song(s); %d already present; %d skipped; %d failed.",
        verb,
        len(result.downloaded),
        len(result.adopted),
        result.skipped,
        len(result.failed),
    )
    if result.failed:
        sys.exit(1)


@cli.command()
@_connection_options
def check(url, api_key, username, password, token, salt, insecure, timeout, debug) -> None:
    """Check that the server is reachable and the credentials work."""
    client = _make_client(url, api_key, username, password, token, salt, insecure, timeout, debug)
    info = client.server_info()
    click.echo(f"Connected to {client.base_url}")
    if info.open_subsonic:
        click.echo(f"Server: {info.server_type or 'unknown'} {info.server_version or ''}".rstrip())
        click.echo(f"API version: {info.api_version}")
        click.echo(f"Extensions: {', '.join(sorted(info.extensions)) or 'none'}")
    else:
        click.echo("Server does not advertise OpenSubsonic support.")
    click.echo("Authentication OK.")


@cli.command("token")
def token_cmd() -> None:
    """Generate a legacy salted token interactively.

    Prefer --api-key, or --password (which salts every request automatically).
    """
    password = getpass("Enter your Subsonic password: ")
    salt = getpass("Enter a salt (at least six characters): ")
    if len(salt) < 6:
        raise click.ClickException("Salt must be at least six characters.")
    click.echo(f"Your API token is: {make_token(password, salt)}")
    click.echo("Use it with --token together with the same --salt.")


def main() -> None:
    cli(prog_name="starsystem")
