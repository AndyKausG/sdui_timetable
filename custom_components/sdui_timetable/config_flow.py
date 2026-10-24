"""Config flow for Sdui integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
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
    CONF_PASSWORD,
    CONF_SLINK,
    CONF_TOKEN,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _credentials_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Schema for username/password login."""
    return vol.Schema(
        {
            vol.Required(CONF_SLINK, default=defaults.get(CONF_SLINK, "")): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            vol.Optional(
                CONF_USER_ID,
                description={"suggested_value": defaults.get(CONF_USER_ID, "")},
            ): str,
        }
    )


def _token_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Schema for manual Bearer token entry."""
    return vol.Schema(
        {
            vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): str,
            vol.Required(CONF_USER_ID, default=defaults.get(CONF_USER_ID, "")): str,
        }
    )


CANDIDATES = "_candidates"


def _user_label(user: dict) -> str:
    """Human readable name of a Sdui user."""
    name = " ".join(
        str(user[k]) for k in ("firstname", "lastname") if user.get(k)
    )
    return name or str(user.get("id"))


def _user_candidates(self_user: dict) -> dict[str, str]:
    """Return {user_id: label} for the account itself and its children."""
    candidates = {}
    for child in self_user.get("childs") or []:
        if isinstance(child, dict) and child.get("id"):
            candidates[str(child["id"])] = _user_label(child)
    if self_user.get("id"):
        candidates[str(self_user["id"])] = f"{_user_label(self_user)} (eigener Account / own account)"
    return candidates


def _select_user_schema(candidates: dict[str, str]) -> vol.Schema:
    """Schema to choose whose timetable is shown."""
    return vol.Schema(
        {
            vol.Required(CONF_USER_ID, default=next(iter(candidates))): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=uid, label=f"{label} ({uid})")
                        for uid, label in candidates.items()
                    ]
                )
            )
        }
    )


async def _async_validate(
    hass: HomeAssistant, user_input: dict[str, Any], use_credentials: bool
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Validate input and return (entry data, errors)."""
    session = async_get_clientsession(hass)
    user_id = (user_input.get(CONF_USER_ID) or "").strip()

    try:
        if use_credentials:
            slink = normalize_slink(user_input[CONF_SLINK])
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            result = await async_login(slink, username, password)
            credentials = (slink, username, password)
            data = {
                CONF_SLINK: slink,
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
            }
            if not user_id:
                client = SduiApiClient.from_login(result, session, "self", credentials)
                candidates = _user_candidates(await client.fetch_self())
                if not candidates:
                    raise SduiApiError("Could not determine user_id from /users/self")
                # Children first: parents usually want a child's timetable
                user_id = next(iter(candidates))
                if len(candidates) > 1:
                    data[CANDIDATES] = candidates
            client = SduiApiClient.from_login(result, session, user_id, credentials)
        else:
            token = user_input[CONF_TOKEN].strip()
            client = SduiApiClient(token, session, user_id=user_id)
            data = {}
        await client.validate_token()
    except SduiAuthError as exc:
        _LOGGER.debug("Sdui authentication failed: %s", exc)
        return None, {"base": "invalid_auth"}
    except SduiApiError as exc:
        _LOGGER.warning("Sdui login/connection failed: %s", exc)
        return None, {"base": "cannot_connect"}
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error during Sdui config flow")
        return None, {"base": "unknown"}

    data[CONF_TOKEN] = client.token
    data[CONF_USER_ID] = client.user_id
    return data, {}


class SduiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sdui."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Let the user pick the login method."""
        return self.async_show_menu(step_id="user", menu_options=["credentials", "token"])

    async def async_step_credentials(self, user_input=None):
        """Login with school, username and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await _async_validate(self.hass, user_input, True)
            if data is not None:
                if CANDIDATES in data:
                    self._pending = data
                    return await self.async_step_select_user()
                return await self._async_create_or_abort(data)

        return self.async_show_form(
            step_id="credentials",
            data_schema=_credentials_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_select_user(self, user_input=None):
        """Choose whose timetable is shown (parent accounts)."""
        candidates = self._pending[CANDIDATES]
        if user_input is not None:
            data = {k: v for k, v in self._pending.items() if k != CANDIDATES}
            data[CONF_USER_ID] = user_input[CONF_USER_ID]
            return await self._async_create_or_abort(
                data, candidates.get(user_input[CONF_USER_ID])
            )
        return self.async_show_form(
            step_id="select_user", data_schema=_select_user_schema(candidates)
        )

    async def async_step_token(self, user_input=None):
        """Login with a manually copied Bearer token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await _async_validate(self.hass, user_input, False)
            if data is not None:
                return await self._async_create_or_abort(data)

        return self.async_show_form(
            step_id="token",
            data_schema=_token_schema(user_input or {}),
            errors=errors,
        )

    async def _async_create_or_abort(self, data: dict[str, Any], name: str | None = None):
        """Create the entry unless the user is already configured."""
        data = {k: v for k, v in data.items() if k != CANDIDATES}
        await self.async_set_unique_id(data[CONF_USER_ID])
        self._abort_if_unique_id_configured()
        title = f"Sdui {name} ({data[CONF_USER_ID]})" if name else f"Sdui (user {data[CONF_USER_ID]})"
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start re-authentication after the token expired or login failed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for credentials again (also migrates token-only entries)."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await _async_validate(
                self.hass, {CONF_USER_ID: entry.data.get(CONF_USER_ID), **user_input}, True
            )
            if data is not None:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, **data}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SLINK, default=entry.data.get(CONF_SLINK, "")
                    ): str,
                    vol.Required(
                        CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
            description_placeholders={"user_id": str(entry.data.get(CONF_USER_ID))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return SduiOptionsFlow()


class SduiOptionsFlow(config_entries.OptionsFlow):
    """Change login method, credentials, token or user_id."""

    async def async_step_init(self, user_input=None):
        """Let the user pick the login method."""
        return self.async_show_menu(step_id="init", menu_options=["credentials", "token"])

    async def async_step_credentials(self, user_input=None):
        """Update username/password login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await _async_validate(self.hass, user_input, True)
            if data is not None:
                if CANDIDATES in data:
                    self._pending = data
                    return await self.async_step_select_user()
                return self._async_save(data)

        return self.async_show_form(
            step_id="credentials",
            data_schema=_credentials_schema(user_input or self.config_entry.data),
            errors=errors,
        )

    async def async_step_select_user(self, user_input=None):
        """Choose whose timetable is shown (parent accounts)."""
        candidates = self._pending[CANDIDATES]
        if user_input is not None:
            data = {k: v for k, v in self._pending.items() if k != CANDIDATES}
            data[CONF_USER_ID] = user_input[CONF_USER_ID]
            return self._async_save(data)
        return self.async_show_form(
            step_id="select_user", data_schema=_select_user_schema(candidates)
        )

    async def async_step_token(self, user_input=None):
        """Update manual Bearer token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await _async_validate(self.hass, user_input, False)
            if data is not None:
                # Drop stored credentials: entry is token-only from now on
                return self._async_save(
                    {**data, CONF_SLINK: None, CONF_USERNAME: None, CONF_PASSWORD: None}
                )

        return self.async_show_form(
            step_id="token",
            data_schema=_token_schema(user_input or self.config_entry.data),
            errors=errors,
        )

    @callback
    def _async_save(self, data: dict[str, Any]):
        """Write new data to the entry and reload it."""
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, **data}
        )
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.async_create_entry(title="", data={})
