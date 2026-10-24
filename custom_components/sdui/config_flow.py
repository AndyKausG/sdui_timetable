"""Config flow for Sdui integration."""
from __future__ import annotations

import logging
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

from .api import SduiApiClient, SduiApiError, SduiAuthError, async_login, normalize_slink
from .const import (
    CONF_ACCOUNT_ID,
    CONF_PASSWORD,
    CONF_SLINK,
    CONF_STUDENTS,
    CONF_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


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
    children = {
        str(child["id"]): _user_name(child)
        for child in self_user.get("childs") or []
        if isinstance(child, dict) and child.get("id")
    }
    return children or {str(self_user["id"]): _user_name(self_user)}


def _students_schema(available: dict[str, str], selected: list[str]) -> vol.Schema:
    """Multi-select of the students to add."""
    return vol.Schema(
        {
            vol.Required(CONF_STUDENTS, default=selected): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=uid, label=f"{name} ({uid})")
                        for uid, name in available.items()
                    ],
                    multiple=True,
                )
            )
        }
    )


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
    self_user = await client.fetch_self()
    if not self_user.get("id"):
        raise SduiApiError("Could not read the account (/users/self)")
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
        self._title = "Sdui"

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Login with school, username and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data, self_user, _ = await _async_login(self.hass, user_input)
            except Exception as exc:  # noqa: BLE001
                errors["base"] = _error_key(exc)
            else:
                await self.async_set_unique_id(data[CONF_ACCOUNT_ID])
                self._abort_if_unique_id_configured()
                self._data = data
                self._available = _students_of(self_user)
                self._title = f"Sdui {_user_name(self_user)}"
                if len(self._available) == 1:
                    return self._create(list(self._available))
                return await self.async_step_students()

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_students(self, user_input=None) -> ConfigFlowResult:
        """Choose the students (children of a parent account)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_STUDENTS]:
                return self._create(user_input[CONF_STUDENTS])
            errors["base"] = "no_students"
        return self.async_show_form(
            step_id="students",
            data_schema=_students_schema(self._available, list(self._available)),
            errors=errors,
        )

    @callback
    def _create(self, selected: list[str]) -> ConfigFlowResult:
        """Create the entry with the selected students."""
        students = {uid: self._available[uid] for uid in selected}
        return self.async_create_entry(
            title=self._title, data={**self._data, CONF_STUDENTS: students}
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
            description_placeholders={"account": entry.title},
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

        try:
            self_user = await entry.runtime_data.client.fetch_self()
            available = {**current, **_students_of(self_user)}
        except Exception as exc:  # noqa: BLE001
            # Offline: offer at least the current selection
            _error_key(exc)
            available = dict(current)

        if user_input is not None:
            if user_input[CONF_STUDENTS]:
                return self.async_create_entry(
                    data={
                        CONF_STUDENTS: {
                            uid: available[uid] for uid in user_input[CONF_STUDENTS]
                        }
                    }
                )
            errors["base"] = "no_students"

        return self.async_show_form(
            step_id="init",
            data_schema=_students_schema(available, list(current)),
            errors=errors,
        )
