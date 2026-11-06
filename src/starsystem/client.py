"""A small Subsonic / OpenSubsonic API client."""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

from starsystem import __version__

log = logging.getLogger(__name__)

CLIENT_NAME = "starsystem"
# Highest Subsonic API version we speak. OpenSubsonic servers all implement at least this.
API_VERSION = "1.16.1"

# Error codes defined by Subsonic / OpenSubsonic.
ERR_WRONG_CREDENTIALS = 40
ERR_TOKEN_AUTH_UNSUPPORTED = 41
ERR_AUTH_MECHANISM_UNSUPPORTED = 42
ERR_CONFLICTING_AUTH = 43
ERR_INVALID_API_KEY = 44


class SubsonicError(Exception):
    """The server responded, but with a Subsonic-level error."""

    def __init__(self, code: int, message: str, help_url: str | None = None) -> None:
        self.code = code
        self.message = message
        self.help_url = help_url
        text = f"Subsonic error {code}: {message}"
        if help_url:
            text += f" (see {help_url})"
        super().__init__(text)


class ClientError(Exception):
    """Transport problems or responses we could not understand."""


class ServerUnreachable(ClientError):
    """Could not connect to the server at all (DNS, refused, timeout, TLS)."""


class Auth(Protocol):
    def params(self) -> dict[str, str]: ...


@dataclass(frozen=True)
class ApiKeyAuth:
    """OpenSubsonic `apiKeyAuthentication` extension. Must not be combined with u/p/t/s."""

    api_key: str

    def params(self) -> dict[str, str]:
        return {"apiKey": self.api_key}


@dataclass(frozen=True)
class PasswordAuth:
    """Classic Subsonic token auth, with a fresh random salt for every request."""

    username: str
    password: str = field(repr=False)

    def params(self) -> dict[str, str]:
        salt = secrets.token_hex(8)
        return {"u": self.username, "t": make_token(self.password, salt), "s": salt}


@dataclass(frozen=True)
class TokenAuth:
    """A pre-computed token and the salt used to generate it (legacy)."""

    username: str
    token: str = field(repr=False)
    salt: str

    def params(self) -> dict[str, str]:
        return {"u": self.username, "t": self.token, "s": self.salt}


def make_token(password: str, salt: str) -> str:
    return hashlib.md5((password + salt).encode("utf-8")).hexdigest()


@dataclass
class ServerInfo:
    api_version: str
    open_subsonic: bool = False
    server_type: str | None = None
    server_version: str | None = None
    extensions: dict[str, list[int]] = field(default_factory=dict)

    def supports(self, extension: str) -> bool:
        return extension in self.extensions


class SubsonicClient:
    def __init__(
        self,
        base_url: str,
        auth: Auth,
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.auth = auth
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.verify = verify_ssl
        self.session.headers["User-Agent"] = f"{CLIENT_NAME}/{__version__}"
        self._server_info: ServerInfo | None = None

    # -- low level ----------------------------------------------------------

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/rest/{endpoint}.view"

    def _request(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        authenticate: bool = True,
        stream: bool = False,
    ) -> requests.Response:
        all_params: dict[str, Any] = {"c": CLIENT_NAME, "v": API_VERSION, "f": "json"}
        if authenticate:
            all_params.update(self.auth.params())
        all_params.update(params or {})

        # Prefer form POST when the server advertises it: it keeps credentials out of URLs
        # (and therefore out of server/proxy access logs).
        info = self._server_info
        use_post = authenticate and info is not None and info.supports("formPost")
        try:
            if use_post:
                response = self.session.post(
                    self._url(endpoint), data=all_params, timeout=self.timeout, stream=stream
                )
            else:
                response = self.session.get(
                    self._url(endpoint), params=all_params, timeout=self.timeout, stream=stream
                )
            response.raise_for_status()
        except (requests.ConnectionError, requests.Timeout) as e:
            raise ServerUnreachable(f"Could not reach {self.base_url}: {e}") from e
        except requests.RequestException as e:
            raise ClientError(f"Request to {endpoint} failed: {e}") from e
        return response

    def _call(
        self, endpoint: str, params: Mapping[str, Any] | None = None, *, authenticate: bool = True
    ) -> dict[str, Any]:
        response = self._request(endpoint, params, authenticate=authenticate)
        return parse_envelope(response)

    # -- API ----------------------------------------------------------------

    def server_info(self) -> ServerInfo:
        """Discover what the server supports. getOpenSubsonicExtensions needs no auth."""
        if self._server_info is not None:
            return self._server_info
        info = ServerInfo(api_version="unknown")
        try:
            body = self._call("getOpenSubsonicExtensions", authenticate=False)
        except ServerUnreachable:
            raise
        except (ClientError, SubsonicError) as e:
            log.debug("Server does not appear to support OpenSubsonic: %s", e)
        else:
            info = ServerInfo(
                api_version=body.get("version", "unknown"),
                open_subsonic=bool(body.get("openSubsonic")),
                server_type=body.get("type"),
                server_version=body.get("serverVersion"),
                extensions={
                    ext["name"]: list(ext.get("versions", []))
                    for ext in body.get("openSubsonicExtensions") or []
                    if isinstance(ext, dict) and "name" in ext
                },
            )
        self._server_info = info
        return info

    def check_auth(self) -> None:
        """Fail early, with a useful message, if the configured auth can't work."""
        info = self.server_info()
        if isinstance(self.auth, ApiKeyAuth) and not info.supports("apiKeyAuthentication"):
            raise ClientError(
                "This server does not advertise the OpenSubsonic apiKeyAuthentication "
                "extension; use --user with --password instead."
            )
        self.ping()

    def ping(self) -> dict[str, Any]:
        return self._call("ping")

    def get_starred_songs(self) -> list[dict[str, Any]]:
        body = self._call("getStarred2")
        songs = (body.get("starred2") or {}).get("song") or []
        if isinstance(songs, dict):  # some servers collapse single-element lists
            songs = [songs]
        return songs

    def download(self, song_id: str, chunk_size: int = 1 << 16) -> Iterator[bytes]:
        response = self._request("download", {"id": song_id}, stream=True)
        content_type = response.headers.get("Content-Type", "")
        # Errors from the download endpoint come back as a JSON/XML envelope, not audio.
        if content_type.startswith(("application/json", "text/xml", "application/xml")):
            try:
                parse_envelope(response)
            finally:
                response.close()
            raise ClientError(f"Expected audio for song {song_id}, got {content_type}")
        try:
            yield from response.iter_content(chunk_size=chunk_size)
        except requests.RequestException as e:
            raise ClientError(f"Download of song {song_id} failed: {e}") from e
        finally:
            response.close()


def parse_envelope(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()["subsonic-response"]
    except (ValueError, KeyError, TypeError) as e:
        raise ClientError(f"Unexpected (non-Subsonic) response from {response.url}") from e
    if body.get("status") != "ok" or "error" in body:
        err = body.get("error") or {}
        raise SubsonicError(
            int(err.get("code", 0)), err.get("message", "unknown error"), err.get("helpUrl")
        )
    return body


def normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"https://{url}"
    # Accept URLs people paste from the browser or other clients, e.g. https://host/rest
    if url.endswith("/rest"):
        url = url[: -len("/rest")]
    return url
