"""Sdui API client."""
from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from urllib.parse import parse_qs, quote, urljoin, urlparse

import aiohttp
from yarl import URL

from .const import API_BASE, IDP_RETURN_URL

_LOGGER = logging.getLogger(__name__)

HEADERS = {
    "Host": "api.sdui.app",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://sdui.app",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Frontend-Version": "2024.4.1@656f3725032525caf2fba2804424e968b5ba456b",
    "Released-At": "2025-09-30 17:04:17",
}

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "User-Agent": HEADERS["User-Agent"],
}

TIMEOUT = aiohttp.ClientTimeout(total=30)
REDIRECT_HOP_LIMIT = 8
# Renew the token this many seconds before it expires
TOKEN_EXPIRY_MARGIN = 300

META_REFRESH_RE = re.compile(
    r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]*content=[\"'][^\"']*url=([^\"'>]+)[\"']",
    re.IGNORECASE,
)
CSRF_TOKEN_RE = re.compile(r'"csrfToken":"([^"]+)"', re.IGNORECASE)


class SduiAuthError(Exception):
    """Raised on 401/403 responses or invalid credentials."""


class SduiApiError(Exception):
    """Raised on other API errors."""


def _decode_jwt_claims(token: str) -> dict:
    """Decode the payload of a JWT without verifying it."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Not a valid JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def extract_user_id_from_token(token: str) -> str:
    """Extract user_id (sub claim) from JWT Bearer token."""
    try:
        return str(_decode_jwt_claims(token)["sub"])
    except Exception as exc:  # noqa: BLE001
        raise SduiAuthError(f"Cannot extract user_id from token: {exc}") from exc


def token_expires_at(token: str) -> float | None:
    """Return the exp claim of a JWT as unix timestamp, or None if unknown."""
    try:
        exp = _decode_jwt_claims(token).get("exp")
    except Exception:  # noqa: BLE001
        return None
    return float(exp) if exp else None


def normalize_slink(value: str) -> str:
    """Turn a school URL (https://sdui.app/<slink>/login) or slink into a slink."""
    value = value.strip()
    if "://" in value or value.startswith("sdui.app"):
        if "://" not in value:
            value = f"https://{value}"
        parts = [p for p in urlparse(value).path.split("/") if p]
        value = parts[0] if parts else ""
    return value.strip("/").lower()


def _location(resp: aiohttp.ClientResponse) -> str | None:
    """Return the Location header of a redirect response."""
    if resp.status in (301, 302, 303, 307, 308):
        return resp.headers.get("Location")
    return None


async def _find_school(session: aiohttp.ClientSession, slink: str) -> dict:
    """Look up a school by slink."""
    async with session.get(
        f"{API_BASE}/find-schools", params={"slink": slink}, headers=HEADERS
    ) as resp:
        if resp.status == 404:
            raise SduiApiError(f"School '{slink}' not found")
        if resp.status != 200:
            raise SduiApiError(f"School lookup failed with status {resp.status}")
        data = (await resp.json()).get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        raise SduiApiError(f"School '{slink}' not found")
    return data


async def _login_password(
    session: aiohttp.ClientSession, slink: str, username: str, password: str
) -> str:
    """Classic Sdui login with username/e-mail and password."""
    async with session.post(
        f"{API_BASE}/auth/login",
        json={
            "identifier": username,
            "password": password,
            "slink": slink,
            "showError": True,
        },
        headers=HEADERS,
    ) as resp:
        if resp.status in (400, 401, 403, 422):
            raise SduiAuthError(f"Login rejected. Status: {resp.status}")
        if resp.status != 200:
            raise SduiApiError(f"Login failed with status {resp.status}")
        body = await resp.json()
    token = (body.get("data") or {}).get("access_token") or body.get("access_token")
    if not token:
        raise SduiApiError("Login succeeded but no access token was returned")
    return token


async def _login_idp(
    session: aiohttp.ClientSession,
    slink: str,
    username: str,
    password: str,
    idp_uri: str,
) -> str:
    """Login via the school's external identity provider (SSO bridge).

    Mirrors what the browser does on "Einloggen mit ...":
    sdui IdP bridge -> WebUntis login form -> sdui IdP -> /auth/login-idm.
    """
    redirect_uri = f"https://sdui.app/sso-verify/{slink}"
    state = json.dumps(
        {"branding": "UNTIS", "slink": slink, "goBackToParentWindow": False},
        separators=(",", ":"),
    )
    authorize_url = (
        f"{idp_uri}&redirect_uri={quote(redirect_uri, safe='')}"
        f"&state={quote(state, safe='')}"
    )

    async with session.get(authorize_url, headers=BROWSER_HEADERS) as resp:
        page = await resp.text()
    match = META_REFRESH_RE.search(page)
    if not match:
        raise SduiApiError("Identity provider page has no redirect target")
    # content may carry trailing tokens like " target=_top"
    init_url = html.unescape(match.group(1)).strip().split()[0]
    _LOGGER.debug("IdP login page: %s", init_url.split("?")[0])

    async with session.get(init_url, headers=BROWSER_HEADERS) as resp:
        page = await resp.text()
        login_url = resp.url
    if not CSRF_TOKEN_RE.search(page):
        raise SduiApiError(
            f"Unsupported identity provider login page: {login_url.host}"
        )

    school = parse_qs(login_url.query_string).get("school", [slink])[0]
    async with session.post(
        str(login_url.origin().join(URL("/WebUntis/j_spring_security_check"))),
        data={"school": school, "j_username": username, "j_password": password},
        headers={**BROWSER_HEADERS, "X-Requested-With": "XMLHttpRequest"},
        allow_redirects=False,
    ) as resp:
        _LOGGER.debug("IdP credential post: status %s", resp.status)

    # Re-open the authorize URL; with a valid session it redirects back to sdui.app
    callback_url = None
    next_url = init_url
    for _ in range(REDIRECT_HOP_LIMIT):
        async with session.get(
            next_url, headers=BROWSER_HEADERS, allow_redirects=False
        ) as resp:
            location = _location(resp)
        if not location:
            break
        next_url = urljoin(next_url, location)
        if next_url.startswith("https://sdui.app/"):
            callback_url = next_url
            break
    if callback_url is None:
        raise SduiAuthError("Identity provider rejected the credentials")

    query = parse_qs(urlparse(callback_url).query)
    if "code" not in query or "state" not in query:
        raise SduiApiError("Missing code/state in Sdui callback")

    async with session.get(
        IDP_RETURN_URL,
        params={"code": query["code"][0], "state": query["state"][0]},
        headers=BROWSER_HEADERS,
        allow_redirects=False,
    ) as resp:
        location = _location(resp)
    if not location:
        raise SduiApiError("Code exchange at Sdui identity bridge failed")
    code = parse_qs(urlparse(urljoin(IDP_RETURN_URL, location)).query).get("code")
    if not code:
        raise SduiApiError("Missing code in Sdui SSO redirect")

    async with session.post(
        f"{API_BASE}/auth/login-idm",
        json={"code": code[0], "slink": slink},
        headers=HEADERS,
    ) as resp:
        if resp.status in (400, 401, 403, 422):
            raise SduiAuthError(f"Token exchange rejected. Status: {resp.status}")
        if resp.status != 200:
            raise SduiApiError(f"Token exchange failed with status {resp.status}")
        body = await resp.json()
    token = (body.get("data") or {}).get("access_token") or body.get("access_token")
    if not token:
        raise SduiApiError("Token exchange succeeded but no access token was returned")
    return token


async def async_login(slink: str, username: str, password: str) -> str:
    """Log in with school slink + credentials and return a Bearer token.

    Schools with an external identity provider use the SSO bridge first;
    the classic password login is used as fallback.
    """
    # Own session: the SSO bridge relies on cookies, which must not leak
    # into Home Assistant's shared client session.
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(), timeout=TIMEOUT
    ) as session:
        try:
            school = await _find_school(session, slink)
            providers = (school.get("meta") or {}).get(
                "external_identity_providers"
            ) or []
            idp_uri = next(
                (p.get("uri") for p in providers if isinstance(p, dict) and p.get("uri")),
                None,
            )
            if idp_uri is None:
                return await _login_password(session, slink, username, password)
            try:
                return await _login_idp(session, slink, username, password, idp_uri)
            except (SduiAuthError, SduiApiError) as idp_exc:
                _LOGGER.debug("SSO login failed (%s), trying password login", idp_exc)
                try:
                    return await _login_password(session, slink, username, password)
                except SduiAuthError:
                    raise idp_exc from None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise SduiApiError(f"Network error: {exc}") from exc


class SduiApiClient:
    """Async API client for sdui.app."""

    def __init__(
        self,
        token: str | None,
        session: aiohttp.ClientSession,
        user_id: str | None = None,
        credentials: tuple[str, str, str] | None = None,
        token_callback: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        """Initialize the API client.

        credentials: (slink, username, password) for automatic re-login.
        token_callback: called with a freshly obtained token (for persisting).
        """
        self._token = token
        self._session = session
        self._credentials = credentials
        self._token_callback = token_callback
        self._login_lock = asyncio.Lock()
        # Use provided user_id or fall back to extracting from token (for backward compatibility)
        if user_id:
            self._user_id = user_id
        elif token:
            self._user_id = extract_user_id_from_token(token)
        else:
            self._user_id = None

    @property
    def user_id(self) -> str | None:
        """Return the user ID."""
        return self._user_id

    @property
    def token(self) -> str | None:
        """Return the current Bearer token."""
        return self._token

    def _auth_headers(self) -> dict:
        """Return headers including Authorization."""
        return {**HEADERS, "Authorization": f"Bearer {self._token}"}

    def _token_expired(self) -> bool:
        """Return True if the token is missing or about to expire."""
        if not self._token:
            return True
        exp = token_expires_at(self._token)
        return exp is not None and exp - TOKEN_EXPIRY_MARGIN < time.time()

    async def async_login(self, stale_token: str | None = None) -> None:
        """Obtain a new token using the stored credentials."""
        if self._credentials is None:
            raise SduiAuthError("Token expired and no credentials stored for re-login")
        async with self._login_lock:
            # Another caller already renewed the token
            if stale_token is not None and self._token != stale_token:
                return
            _LOGGER.debug("Logging in to Sdui to obtain a new token")
            self._token = await async_login(*self._credentials)
            if self._token_callback is not None:
                result = self._token_callback(self._token)
                if result is not None:
                    await result

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """GET an API path, renewing the token once on expiry/401."""
        if self._credentials is not None and self._token_expired():
            await self.async_login(self._token)

        for attempt in (1, 2):
            token = self._token
            try:
                async with self._session.get(
                    f"{API_BASE}{path}",
                    params=params,
                    headers=self._auth_headers(),
                    timeout=TIMEOUT,
                ) as resp:
                    if resp.status in (401, 403):
                        if attempt == 1 and self._credentials is not None:
                            await self.async_login(token)
                            continue
                        raise SduiAuthError(
                            f"Authentication failed. Status: {resp.status}"
                        )
                    if resp.status != 200:
                        text = await resp.text()
                        raise SduiApiError(
                            f"Unexpected response {resp.status}: {text[:200]}"
                        )
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise SduiApiError(f"Network error: {exc}") from exc
        raise SduiAuthError("Authentication failed after re-login")

    async def fetch_self(self) -> dict:
        """Return the logged-in user (GET /users/self)."""
        return (await self._get("/users/self")).get("data") or {}

    async def fetch_timetable(
        self, begins_at: str, ends_at: str
    ) -> list[dict]:
        """Fetch timetable lessons between two dates (YYYY-MM-DD format)."""
        path = f"/timetables/users/{self._user_id}/timetable"
        _LOGGER.debug("Fetching timetable: %s %s-%s", path, begins_at, ends_at)
        data = await self._get(path, {"begins_at": begins_at, "ends_at": ends_at})
        return (data.get("data") or {}).get("lessons", [])

    async def validate_token(self) -> str:
        """Validate the token by fetching today's timetable. Returns user_id on success."""
        today = datetime.now().strftime("%Y-%m-%d")
        # May return empty lessons, that's fine — just check no auth error
        await self.fetch_timetable(today, today)
        return self._user_id
