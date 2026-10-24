"""The Sdui integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SduiApiClient, SduiApiError, SduiAuthError
from .const import (
    CONF_PASSWORD,
    CONF_SLINK,
    CONF_TOKEN,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
    PLATFORM_CALENDAR,
    PLATFORM_SENSOR,
)
from .coordinator import SduiCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [PLATFORM_SENSOR, PLATFORM_CALENDAR]


def entry_credentials(data: dict) -> tuple[str, str, str] | None:
    """Return (slink, username, password) if the entry uses credential login."""
    if data.get(CONF_SLINK) and data.get(CONF_USERNAME) and data.get(CONF_PASSWORD):
        return data[CONF_SLINK], data[CONF_USERNAME], data[CONF_PASSWORD]
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sdui from a config entry."""
    session = async_get_clientsession(hass)

    @callback
    def _store_token(token: str) -> None:
        """Persist a renewed token so a restart does not need a new login."""
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKEN: token}
        )

    try:
        client = SduiApiClient(
            entry.data.get(CONF_TOKEN),
            session,
            user_id=entry.data.get(CONF_USER_ID),
            credentials=entry_credentials(entry.data),
            token_callback=_store_token,
        )
    except SduiAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    coordinator = SduiCoordinator(hass, client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except SduiAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except SduiApiError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
