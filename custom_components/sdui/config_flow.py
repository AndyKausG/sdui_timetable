"""Config flow for Sdui integration."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import SduiApiClient, SduiApiError, SduiAuthError, async_login, normalize_slink
from .const import (
    CONF_ACCOUNT_ID,
    CONF_EXTRA_IDS,
    CONF_PASSWORD,
    CONF_SLINK,
    CONF_STUDENTS,
    CONF_TOKEN,
    CONF_USERNAME,
    DOMAIN,
    SCHOOL_SEARCH_URL,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
# URLs are not allowed in translations, they are passed as placeholders
PLACEHOLDERS = {"school_search": f"[sdui.app/select-school]({SCHOOL_SEARCH_URL})"}


def _credentials_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Schema for the login form."""
    return vol.Schema(
        {
            vol.Required(CONF_SLINK, default=defaults.get(CONF_SLINK, "")): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        }
    )


def _user_name(user: dict) -> str:
    """Human readable name of a Sdui user."""
    name = " ".join(str(user[k]) for k in ("firstname", "lastname") if user.get(k))
    return name or str(user.get("id"))


def _students_of(self_user: dict) -> dict[str, str]:
    """Return {user_id: name} of the account's children, or the account itself."""
    _LOGGER.debug(
        "users/self keys: %s; childs keys: %s",
        sorted(self_user),
        [sorted(c) for c in self_user.get("childs") or [] if isinstance(c, dict)],
    )
    children = {}
    for key in ("childs", "children"):
        for child in self_user.get(key) or []:
            if isinstance(child, dict) and (uid := child.get("id") or child.get("user_id")):
                children[str(uid)] = _user_name(child)
    return children or {str(self_user["id"]): _user_name(self_user)}


async def _async_fetch_account(client: SduiApiClient) -> dict:
    """Return the logged-in user including its children if available."""
    self_user = await client.fetch_self()
    if not self_user.get("id"):
        raise SduiApiError("Could not read the account (/users/self)")
    if not (self_user.get("childs") or self_user.get("children")):
        # sdui.app lists the children (childs[].id) of a parent account; they
        # may only be part of the full user object
        try:
            full = await client.fetch_user(str(self_user["id"]))
        except (SduiAuthError, SduiApiError) as exc:
            _LOGGER.debug("Could not read /users/%s: %s", self_user["id"], exc)
        else:
            for key in ("childs", "children"):
                if full.get(key):
                    self_user = {**self_user, key: full[key]}
    return self_user


def _students_schema(available: dict[str, str], selected: list[str]) -> vol.Schema:
    """Multi-select of the students plus free input of further timetable ids."""
    return vol.Schema(
        {
            vol.Optional(CONF_STUDENTS, default=selected): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=uid, label=f"{name} ({uid})")
                        for uid, name in available.items()
                    ],
                    multiple=True,
                )
            ),
            vol.Optional(CONF_EXTRA_IDS, default=""): str,
        }
    )


async def _async_students_from_input(
    client: SduiApiClient, available: dict[str, str], user_input: Mapping[str, Any]
) -> dict[str, str]:
    """Build {user_id: name} from the selection and manually entered ids.

    Raises SduiAuthError/SduiApiError if a manual id has no accessible timetable.
    """
    students = {uid: available[uid] for uid in user_input.get(CONF_STUDENTS) or []}
    today = dt_util.now().date().isoformat()
    for uid in re.findall(r"\d+", user_input.get(CONF_EXTRA_IDS) or ""):
        if uid in students:
            continue
        await client.fetch_timetable(uid, today, today)
        try:
            name = _user_name(await client.fetch_user(uid))
        except (SduiAuthError, SduiApiError):
            name = uid
        students[uid] = name if name != uid else f"Schüler {uid}"
    return students


async def _async_login(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> tuple[dict[str, Any], dict, SduiApiClient]:
    """Log in and return (entry data, /users/self, client). Raises Sdui errors."""
    slink = normalize_slink(user_input[CONF_SLINK])
    username = user_input[CONF_USERNAME].strip()
    password = user_input[CONF_PASSWORD]
    credentials = (slink, username, password)
    client = SduiApiClient(
        async_get_clientsession(hass),
        credentials,
        login=await async_login(*credentials),
    )
    self_user = await _async_fetch_account(client)
    data = {
        CONF_SLINK: slink,
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
        CONF_ACCOUNT_ID: str(self_user["id"]),
        CONF_TOKEN: client.token,
    }
    return data, self_user, client


def _error_key(exc: Exception) -> str:
    """Map an exception to a translation key."""
    if isinstance(exc, SduiAuthError):
        _LOGGER.debug("Sdui authentication failed: %s", exc)
        return "invalid_auth"
    if isinstance(exc, SduiApiError):
        _LOGGER.warning("Sdui login/connection failed: %s", exc)
        return "cannot_connect"
    _LOGGER.exception("Unexpected error during Sdui config flow", exc_info=exc)
    return "unknown"


class SduiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sdui."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._data: dict[str, Any] = {}
        self._available: dict[str, str] = {}
        self._client: SduiApiClient | None = None
        self._title = "Sdui"

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Login with school, username and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data, self_user, self._client = await _async_login(self.hass, user_input)
            except Exception as exc:  # noqa: BLE001
                errors["base"] = _error_key(exc)
            else:
                await self.async_set_unique_id(data[CONF_ACCOUNT_ID])
                self._abort_if_unique_id_configured()
                self._data = data
                self._available = _students_of(self_user)
                self._title = f"Sdui {_user_name(self_user)}"
                return await self.async_step_students()

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(user_input or {}),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def async_step_students(self, user_input=None) -> ConfigFlowResult:
        """Choose the students (children of a parent account)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                students = await _async_students_from_input(
                    self._client, self._available, user_input
                )
            except (SduiAuthError, SduiApiError) as exc:
                _LOGGER.warning("Timetable id not accessible: %s", exc)
                errors["base"] = "invalid_student"
            else:
                if students:
                    data = {**self._data, CONF_TOKEN: self._client.token}
                    return self.async_create_entry(
                        title=self._title, data={**data, CONF_STUDENTS: students}
                    )
                errors["base"] = "no_students"
        return self.async_show_form(
            step_id="students",
            data_schema=_students_schema(self._available, list(self._available)),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the login failed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        """Ask for the credentials again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data, _, _ = await _async_login(self.hass, user_input)
            except Exception as exc:  # noqa: BLE001
                errors["base"] = _error_key(exc)
            else:
                await self.async_set_unique_id(data[CONF_ACCOUNT_ID])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, **data}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(user_input or entry.data),
            errors=errors,
            description_placeholders={**PLACEHOLDERS, "account": entry.title},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return SduiOptionsFlow()


class SduiOptionsFlow(OptionsFlow):
    """Change the selected students."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Show all students of the account, preselect the current ones."""
        entry = self.config_entry
        current: dict[str, str] = entry.options.get(CONF_STUDENTS, entry.data[CONF_STUDENTS])
        errors: dict[str, str] = {}

        client = entry.runtime_data.client
        try:
            self_user = await _async_fetch_account(client)
            available = {**current, **_students_of(self_user)}
        except Exception as exc:  # noqa: BLE001
            # Offline: offer at least the current selection
            _error_key(exc)
            available = dict(current)

        if user_input is not None:
            try:
                students = await _async_students_from_input(client, available, user_input)
            except (SduiAuthError, SduiApiError) as exc:
                _LOGGER.warning("Timetable id not accessible: %s", exc)
                errors["base"] = "invalid_student"
            else:
                if students:
                    return self.async_create_entry(data={CONF_STUDENTS: students})
                errors["base"] = "no_students"

        return self.async_show_form(
            step_id="init",
            data_schema=_students_schema(available, list(current)),
            errors=errors,
        )
