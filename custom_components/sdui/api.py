"""Sdui API client."""
from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import hashlib
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import API_BASE, IAM_BASE, IAM_CLIENT_ID, IAM_REDIRECT_URI

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
# Renew the token this many seconds before it expires
TOKEN_EXPIRY_MARGIN = 60

ISSUER_RE = re.compile(r"(https://[^\s\"'/]+/(?:auth/)?realms/[^\s\"'/?#]+)")
# Keycloak realm ids look like s-aadbb1dae3204654
REALM_RE = re.compile(r"^s-[0-9a-f]{16}$")
# Keycloakify themes expose the form target in kcContext, classic themes in <form>
KC_LOGIN_ACTION_RE = re.compile(r'"loginAction":\s*"([^"]+)"')
KC_FORM_ACTION_RE = re.compile(
    r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', re.IGNORECASE
)


@dataclass
class LoginResult:
    """Tokens obtained by a login."""

    access_token: str
    refresh_token: str | None = None
    token_url: str | None = None


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


def token_expires_at(token: str) -> float | None:
    """Return the exp claim of a JWT as unix timestamp, or None if unknown."""
    try:
        exp = _decode_jwt_claims(token).get("exp")
    except Exception:  # noqa: BLE001
        return None
    return float(exp) if exp else None


def normalize_slink(value: str) -> str:
    """Turn a school URL (https://sdui.app/<slink>/login) or slink into a slink.

    A Keycloak login URL (https://iam.../realms/<realm>/...) is reduced to its
    issuer URL and used directly for the OIDC login.
    """
    value = value.strip()
    if match := ISSUER_RE.search(value):
        return match.group(1)
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


def _find_issuer(school: dict) -> str | None:
    """Find the Keycloak issuer (https://iam.../realms/<realm>) in school data."""
    # Known format: meta.group_iam[].uri / realm_id
    for iam in (school.get("meta") or {}).get("group_iam") or []:
        if not isinstance(iam, dict):
            continue
        if match := ISSUER_RE.search(iam.get("uri") or ""):
            return match.group(1)
        if REALM_RE.match(iam.get("realm_id") or ""):
            return f"{IAM_BASE}/realms/{iam['realm_id']}"
    # Fallback: search the whole response
    stack: list = [school]
    realm = None
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(value, str) and REALM_RE.match(value):
                    realm = realm or value
                stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and (match := ISSUER_RE.search(item)):
            return match.group(1)
    return f"{IAM_BASE}/realms/{realm}" if realm else None


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def _token_request(
    session: aiohttp.ClientSession, token_url: str, data: dict
) -> LoginResult:
    """POST to the OIDC token endpoint."""
    async with session.post(
        token_url, data={"client_id": IAM_CLIENT_ID, **data}, timeout=TIMEOUT
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status in (400, 401):
            raise SduiAuthError(
                f"Token request rejected: {body.get('error')} {body.get('error_description', '')}"
            )
        if resp.status != 200:
            raise SduiApiError(f"Token request failed with status {resp.status}")
    if not body.get("access_token"):
        raise SduiApiError("Token response contains no access token")
    return LoginResult(body["access_token"], body.get("refresh_token"), token_url)


async def _login_keycloak(
    session: aiohttp.ClientSession, issuer: str, username: str, password: str
) -> LoginResult:
    """OIDC authorization code flow with PKCE against the Sdui Keycloak."""
    verifier, challenge = _pkce_pair()
    oidc = f"{issuer}/protocol/openid-connect"
    async with session.get(
        f"{oidc}/auth",
        params={
            "client_id": IAM_CLIENT_ID,
            "redirect_uri": IAM_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid offline_access",
            "state": secrets.token_hex(16),
            "nonce": secrets.token_hex(16),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        headers=BROWSER_HEADERS,
    ) as resp:
        if resp.status == 400:
            # offline_access may not be allowed for the client: retry without
            page = None
        else:
            page = await resp.text()
            if resp.status != 200:
                raise SduiApiError(f"Login page returned status {resp.status}")
    if page is None:
        async with session.get(
            f"{oidc}/auth",
            params={
                "client_id": IAM_CLIENT_ID,
                "redirect_uri": IAM_REDIRECT_URI,
                "response_type": "code",
                "scope": "openid",
                "state": secrets.token_hex(16),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            headers=BROWSER_HEADERS,
        ) as resp:
            page = await resp.text()
            if resp.status != 200:
                raise SduiApiError(f"Login page returned status {resp.status}")

    match = KC_LOGIN_ACTION_RE.search(page) or KC_FORM_ACTION_RE.search(page)
    if not match:
        raise SduiApiError("Keycloak login form not found")
    action = html.unescape(match.group(1).replace("\\/", "/"))
    _LOGGER.debug("Keycloak login action: %s", action.split("?")[0])

    async with session.post(
        action,
        data={"username": username, "password": password, "credentialId": ""},
        headers=BROWSER_HEADERS,
        allow_redirects=False,
    ) as resp:
        location = _location(resp)
        status = resp.status
    if not location or not location.startswith(IAM_REDIRECT_URI):
        _LOGGER.debug("Keycloak login: status %s, location %s", status, location)
        raise SduiAuthError("Keycloak rejected the credentials")

    query = parse_qs(urlparse(location).query)
    if "code" not in query:
        raise SduiAuthError(f"Keycloak returned no code: {query.get('error')}")

    return await _token_request(
        session,
        f"{oidc}/token",
        {
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": IAM_REDIRECT_URI,
            "code_verifier": verifier,
        },
    )


async def async_refresh(
    session: aiohttp.ClientSession, token_url: str, refresh_token: str
) -> LoginResult:
    """Renew tokens with a Keycloak refresh token."""
    try:
        result = await _token_request(
            session,
            token_url,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise SduiApiError(f"Network error: {exc}") from exc
    # Keycloak may not rotate the refresh token
    result.refresh_token = result.refresh_token or refresh_token
    return result


async def async_login(slink: str, username: str, password: str) -> LoginResult:
    """Log in with school slink + credentials and return the tokens.

    Keycloak (OIDC) if the school has a realm, else the classic password login.
    """
    # Own session: the login flows rely on cookies, which must not leak
    # into Home Assistant's shared client session.
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(), timeout=TIMEOUT
    ) as session:
        try:
            if slink.startswith("https://"):
                return await _login_keycloak(session, slink, username, password)

            school = await _find_school(session, slink)
            issuer = _find_issuer(school)
            if issuer is not None:
                _LOGGER.debug("Using Keycloak login: %s", issuer)
                return await _login_keycloak(session, issuer, username, password)
            _LOGGER.debug("No Keycloak realm in school data, using password login")
            return LoginResult(
                await _login_password(session, slink, username, password)
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise SduiApiError(f"Network error: {exc}") from exc


class SduiApiClient:
    """Async API client for sdui.app, bound to one account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        credentials: tuple[str, str, str],
        login: LoginResult | None = None,
        token_callback: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        """Initialize the API client.

        credentials: (slink, username, password) for (re-)login.
        login: result of a login that was already done (e.g. in the config flow).
        token_callback: called with a freshly obtained token (for persisting).
        """
        self._session = session
        self._credentials = credentials
        self._token_callback = token_callback
        self._login_lock = asyncio.Lock()
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._token_url: str | None = None
        if login is not None:
            self._apply(login)

    @property
    def token(self) -> str | None:
        """Return the current Bearer token."""
        return self._token

    def set_token(self, token: str | None) -> None:
        """Use a stored token (it is renewed automatically when expired)."""
        self._token = token

    def _apply(self, result: LoginResult) -> None:
        """Take over tokens from a login or refresh."""
        self._token = result.access_token
        self._refresh_token = result.refresh_token
        self._token_url = result.token_url

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
        """Obtain a new token via refresh token or the stored credentials."""
        async with self._login_lock:
            # Another caller already renewed the token
            if self._token != stale_token:
                return
            result = None
            if self._refresh_token and self._token_url:
                try:
                    result = await async_refresh(
                        self._session, self._token_url, self._refresh_token
                    )
                    _LOGGER.debug("Token renewed with refresh token")
                except SduiAuthError as exc:
                    _LOGGER.debug("Refresh token rejected (%s), logging in again", exc)
            if result is None:
                _LOGGER.debug("Logging in to Sdui to obtain a new token")
                result = await async_login(*self._credentials)
            self._apply(result)
            if self._token_callback is not None:
                callback_result = self._token_callback(self._token)
                if callback_result is not None:
                    await callback_result

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """GET an API path, renewing the token on expiry and once on 401."""
        if self._token_expired():
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
                        if attempt == 1:
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

    async def fetch_user(self, user_id: str) -> dict:
        """Return a user (GET /users/{id})."""
        return (await self._get(f"/users/{user_id}")).get("data") or {}

    async def fetch_timetable(
        self, user_id: str, begins_at: str, ends_at: str
    ) -> list[dict]:
        """Fetch a user's lessons between two dates (YYYY-MM-DD format)."""
        path = f"/timetables/users/{user_id}/timetable"
        _LOGGER.debug("Fetching timetable: %s %s-%s", path, begins_at, ends_at)
        data = await self._get(path, {"begins_at": begins_at, "ends_at": ends_at})
        return (data.get("data") or {}).get("lessons", [])
