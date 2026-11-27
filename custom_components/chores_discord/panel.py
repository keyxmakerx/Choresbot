"""The training page: a sidebar panel plus the websocket commands it calls.

The page is a single plain JavaScript file (no build step) served by Home
Assistant itself. It shows the live snapshot with the region, what the bot
thinks of each bin and how sure it is, the snapshots waiting for an answer,
and every labelled sample, which can be relabelled or deleted. Admin only.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, PANEL_COMPONENT, PANEL_STATIC, PANEL_URL

_LOGGER = logging.getLogger(__name__)

_DATA = f"{DOMAIN}_panel"
_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the static file, websocket commands and sidebar entry (once)."""
    data = hass.data.setdefault(_DATA, {"static": False, "panel": False})
    if not data["static"]:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC, _FRONTEND_DIR, cache_headers=False)]
        )
        for command in (
            ws_overview, ws_snapshot, ws_samples, ws_label, ws_relabel, ws_delete, ws_set_region,
        ):
            websocket_api.async_register_command(hass, command)
        data["static"] = True
    if not data["panel"]:
        mtime = int(os.path.getmtime(os.path.join(_FRONTEND_DIR, "chores-panel.js")))
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name=PANEL_COMPONENT,
            frontend_url_path=PANEL_URL,
            module_url=f"{PANEL_STATIC}/chores-panel.js?v={mtime}",
            sidebar_title="Chores",
            sidebar_icon="mdi:delete-variant",
            require_admin=True,
            config={},
        )
        data["panel"] = True


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    data = hass.data.get(_DATA)
    if data and data["panel"]:
        frontend.async_remove_panel(hass, PANEL_URL)
        data["panel"] = False


def _coordinator(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise HomeAssistantError("The Chores Discord Bot isn't loaded")


async def _run(connection, msg, coro) -> None:
    try:
        connection.send_result(msg["id"], await coro)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Training page command failed")
        connection.send_error(msg["id"], "failed", f"{type(err).__name__}: {err}")


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/overview"})
@websocket_api.async_response
async def ws_overview(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, _coordinator(hass).async_panel_overview())


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/snapshot"})
@websocket_api.async_response
async def ws_snapshot(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, _coordinator(hass).async_panel_snapshot())


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/samples"})
@websocket_api.async_response
async def ws_samples(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, _coordinator(hass).async_panel_samples())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/label",
        vol.Required("sid"): str,
        vol.Required("bin"): vol.In(["trash", "recycling", "all"]),
        vol.Required("label"): vol.In(["out", "in", "skip"]),
    }
)
@websocket_api.async_response
async def ws_label(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def go():
        user = await hass.auth.async_get_user(connection.user.id)
        reply = await _coordinator(hass).async_label(
            msg["sid"], msg["bin"], msg["label"], user.name if user else "Home Assistant", None
        )
        if reply is None:
            raise HomeAssistantError("That snapshot was already answered or has expired")
        return {"reply": reply}

    await _run(connection, msg, go())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/relabel",
        vol.Required("ref_id"): str,
        vol.Required("label"): vol.In(["out", "in"]),
    }
)
@websocket_api.async_response
async def ws_relabel(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def go():
        if not await _coordinator(hass).async_panel_relabel(msg["ref_id"], msg["label"]):
            raise HomeAssistantError("Sample not found")
        return {}

    await _run(connection, msg, go())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/delete", vol.Required("ref_id"): str}
)
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def go():
        if not await _coordinator(hass).async_panel_delete(msg["ref_id"]):
            raise HomeAssistantError("Sample not found")
        return {}

    await _run(connection, msg, go())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_region",
        vol.Required("roi"): vol.All([vol.Coerce(float)], vol.Length(min=4, max=4)),
    }
)
@websocket_api.async_response
async def ws_set_region(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def go():
        await _coordinator(hass).async_panel_set_region(msg["roi"])
        return {}

    await _run(connection, msg, go())
