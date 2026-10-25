"""The Sdui integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SduiApiClient
from .const import (
    CONF_HIDDEN_COURSES,
    CONF_PASSWORD,
    CONF_SLINK,
    CONF_STRIP_CLASS,
    CONF_STUDENTS,
    CONF_TOKEN,
    CONF_USERNAME,
    PLATFORMS,
)
from .coordinator import SduiCoordinator

_LOGGER = logging.getLogger(__name__)

type SduiConfigEntry = ConfigEntry[SduiCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SduiConfigEntry) -> bool:
    """Set up Sdui from a config entry."""

    @callback
    def _store_token(token: str) -> None:
        """Persist a renewed token so a restart within its lifetime needs no login."""
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKEN: token}
        )

    client = SduiApiClient(
        async_get_clientsession(hass),
        (entry.data[CONF_SLINK], entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]),
        token_callback=_store_token,
    )
    client.set_token(entry.data.get(CONF_TOKEN))

    coordinator = SduiCoordinator(
        hass,
        entry,
        client,
        entry.options.get(CONF_STUDENTS, entry.data[CONF_STUDENTS]),
        hidden_courses=entry.options.get(CONF_HIDDEN_COURSES),
        strip_class_prefix=entry.options.get(CONF_STRIP_CLASS, False),
    )
    coordinator.options_snapshot = dict(entry.options)
    # Auth errors raise ConfigEntryAuthFailed (-> reauth), others ConfigEntryNotReady
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SduiConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: SduiConfigEntry) -> None:
    """Reload when the options change (not on token updates)."""
    if entry.runtime_data.options_snapshot != dict(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)
