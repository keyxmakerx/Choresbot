"""Config and options flow for the Chores Discord Bot."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import chores as chores_mod
from .const import (
    CONF_ASK_GAP,
    CONF_ASKS_PER_DAY,
    CONF_BIN_ADMINS,
    CONF_BOT_TOKEN,
    CONF_BUMP_AFTER,
    CONF_CAMERA,
    CONF_CANS_CHANNEL_ID,
    CONF_CANS_CHORE,
    CONF_CANS_ENABLED,
    CONF_CANS_NIGHTS,
    CONF_CHANNEL_ID,
    CONF_CHECK_INTERVAL,
    CONF_CONFIDENCE,
    CONF_CUSTOM_CHORES,
    CONF_DIGEST,
    CONF_DIGEST_TIME,
    CONF_DISABLED_CHORES,
    CONF_MIN_ACCURACY,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_ELEVATION,
    CONF_MIN_LABELS,
    CONF_NAG_MENTION,
    CONF_NAG_TIME,
    CONF_RENAG,
    CONF_ROI_BOTTOM,
    CONF_ROI_LEFT,
    CONF_ROI_RIGHT,
    CONF_ROI_TOP,
    CONF_QUICK,
    CONF_RECYCLING_CHORE,
    CONF_RECYCLING_NIGHTS,
    CONF_RECYCLING_WEEKS,
    DEFAULT_ASK_GAP,
    DEFAULT_ASKS_PER_DAY,
    DEFAULT_BUMP_AFTER,
    DEFAULT_CANS_CHORE,
    DEFAULT_CANS_ENABLED,
    DEFAULT_CANS_NIGHTS,
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_CONFIDENCE,
    DEFAULT_CUSTOM_CHORES,
    DEFAULT_DIGEST,
    DEFAULT_DIGEST_TIME,
    DEFAULT_MIN_ACCURACY,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_MIN_ELEVATION,
    DEFAULT_MIN_LABELS,
    DEFAULT_NAG_TIME,
    DEFAULT_RENAG,
    DEFAULT_ROI,
    DEFAULT_RECYCLING_CHORE,
    DEFAULT_RECYCLING_NIGHTS,
    DEFAULT_RECYCLING_WEEKS,
    DOMAIN,
    MAX_ASKS_PER_DAY,
    MAX_BUMP_AFTER,
    MAX_CHECK_INTERVAL,
    MAX_RENAG,
    MIN_CHECK_INTERVAL,
    NO_CHORE,
    WEEKDAYS,
)


def _number(minimum: float, maximum: float, step: float, unit: str | None = None):
    config: dict[str, Any] = {
        "min": minimum, "max": maximum, "step": step, "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:  # Home Assistant rejects an explicit None here
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**config))


def _select(options: list[tuple[str, str]], *, multiple: bool = False):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=v, label=l) for v, l in options],
            multiple=multiple,
            mode=selector.SelectSelectorMode.LIST if multiple else selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _weekdays():
    return _select([(str(i), name) for i, name in enumerate(WEEKDAYS)], multiple=True)


def _weeks():
    return _select([("every", "Every week"), ("even", "Even weeks"), ("odd", "Odd weeks")])


class ChoresConfigFlow(ConfigFlow, domain=DOMAIN):
    """Bot token and channel, then (optionally) the camera and bin nights."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            channel_id = str(user_input[CONF_CHANNEL_ID]).strip()
            if not channel_id.isdigit():
                errors[CONF_CHANNEL_ID] = "invalid_channel"
            else:
                await self.async_set_unique_id(channel_id)
                self._abort_if_unique_id_configured()
                self._data = {CONF_BOT_TOKEN: user_input[CONF_BOT_TOKEN], CONF_CHANNEL_ID: channel_id}
                return await self.async_step_camera()
        schema = vol.Schema(
            {
                vol.Required(CONF_BOT_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_CHANNEL_ID): selector.TextSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the bot token or channel without removing the integration."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            channel_id = str(user_input[CONF_CHANNEL_ID]).strip()
            if not channel_id.isdigit():
                errors[CONF_CHANNEL_ID] = "invalid_channel"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_BOT_TOKEN: user_input[CONF_BOT_TOKEN], CONF_CHANNEL_ID: channel_id},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_BOT_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_CHANNEL_ID, default=str(entry.data.get(CONF_CHANNEL_ID, ""))
                ): selector.TextSelector(),
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    async def async_step_camera(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick one of Home Assistant's cameras for the bins (or skip)."""
        if user_input is not None:
            camera = user_input.get(CONF_CAMERA) or ""
            options = {
                CONF_CANS_ENABLED: bool(camera),
                CONF_CAMERA: camera,
                CONF_CANS_NIGHTS: [int(d) for d in user_input.get(CONF_CANS_NIGHTS, [])],
                CONF_RECYCLING_NIGHTS: [int(d) for d in user_input.get(CONF_RECYCLING_NIGHTS, [])],
                CONF_RECYCLING_WEEKS: user_input.get(CONF_RECYCLING_WEEKS, DEFAULT_RECYCLING_WEEKS),
            }
            return self.async_create_entry(title="Chores Discord Bot", data=self._data, options=options)
        schema = vol.Schema(
            {
                vol.Optional(CONF_CAMERA): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="camera")
                ),
                vol.Optional(CONF_CANS_NIGHTS, default=[]): _weekdays(),
                vol.Optional(CONF_RECYCLING_NIGHTS, default=[]): _weekdays(),
                vol.Required(CONF_RECYCLING_WEEKS, default=DEFAULT_RECYCLING_WEEKS): _weeks(),
            }
        )
        return self.async_show_form(step_id="camera", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ChoresOptionsFlow()


class ChoresOptionsFlow(OptionsFlow):
    """A menu: the chore list, or the trash-can watcher."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["chores", "cans"])

    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _save(self, changes: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_chores(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"problems": ""}
        current = self._current()
        if user_input is not None:
            _, problems = chores_mod.parse_custom(user_input.get(CONF_CUSTOM_CHORES, ""))
            if len(user_input.get(CONF_QUICK, [])) > chores_mod.MAX_QUICK:
                errors[CONF_QUICK] = "too_many_quick"
            elif problems:
                errors[CONF_CUSTOM_CHORES] = "bad_custom"
                placeholders["problems"] = "; ".join(problems)
            else:
                return self._save(
                    {
                        CONF_CUSTOM_CHORES: user_input.get(CONF_CUSTOM_CHORES, ""),
                        CONF_DISABLED_CHORES: user_input.get(CONF_DISABLED_CHORES, []),
                        CONF_QUICK: user_input.get(CONF_QUICK, [])[: chores_mod.MAX_QUICK],
                        CONF_DIGEST: user_input[CONF_DIGEST],
                        CONF_DIGEST_TIME: str(user_input[CONF_DIGEST_TIME]),
                        CONF_BUMP_AFTER: int(user_input[CONF_BUMP_AFTER]),
                    }
                )
            current = {**current, **user_input}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DISABLED_CHORES, default=list(current.get(CONF_DISABLED_CHORES) or [])
                ): _select([(c.key, f"{c.emoji} {c.name}") for c in chores_mod.DEFAULT_CHORES], multiple=True),
                vol.Optional(
                    CONF_CUSTOM_CHORES,
                    description={"suggested_value": current.get(CONF_CUSTOM_CHORES, DEFAULT_CUSTOM_CHORES)},
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
                vol.Optional(
                    CONF_QUICK, default=list(current.get(CONF_QUICK) or chores_mod.DEFAULT_QUICK)
                ): _select(
                    [(c.key, f"{c.emoji} {c.name}") for c in chores_mod.catalogue(
                        current.get(CONF_CUSTOM_CHORES, ""), list(current.get(CONF_DISABLED_CHORES) or [])
                    )],
                    multiple=True,
                ),
                vol.Required(CONF_DIGEST, default=current.get(CONF_DIGEST, DEFAULT_DIGEST)): selector.BooleanSelector(),
                vol.Required(
                    CONF_DIGEST_TIME, default=current.get(CONF_DIGEST_TIME, DEFAULT_DIGEST_TIME)
                ): selector.TimeSelector(),
                vol.Required(
                    CONF_BUMP_AFTER, default=current.get(CONF_BUMP_AFTER, DEFAULT_BUMP_AFTER)
                ): _number(0, MAX_BUMP_AFTER, 1, "messages"),
            }
        )
        return self.async_show_form(
            step_id="chores", data_schema=schema, errors=errors, description_placeholders=placeholders
        )

    async def async_step_cans(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = self._current()
        if user_input is not None:
            cans_channel = str(user_input.get(CONF_CANS_CHANNEL_ID) or "").strip()
            if user_input.get(CONF_CANS_ENABLED) and not user_input.get(CONF_CAMERA):
                errors[CONF_CAMERA] = "camera_required"
            elif cans_channel and not cans_channel.isdigit():
                errors[CONF_CANS_CHANNEL_ID] = "invalid_channel"
            else:
                changes = {
                    CONF_CANS_ENABLED: user_input[CONF_CANS_ENABLED],
                    CONF_CAMERA: user_input.get(CONF_CAMERA, ""),
                    CONF_CANS_CHANNEL_ID: cans_channel,
                    CONF_CANS_NIGHTS: [int(d) for d in user_input.get(CONF_CANS_NIGHTS, [])],
                    CONF_NAG_TIME: str(user_input[CONF_NAG_TIME]),
                    CONF_RENAG: int(user_input[CONF_RENAG]),
                    CONF_NAG_MENTION: (user_input.get(CONF_NAG_MENTION) or "").strip(),
                    CONF_BIN_ADMINS: (user_input.get(CONF_BIN_ADMINS) or "").strip(),
                    CONF_CANS_CHORE: user_input[CONF_CANS_CHORE],
                    CONF_RECYCLING_NIGHTS: [int(d) for d in user_input.get(CONF_RECYCLING_NIGHTS, [])],
                    CONF_RECYCLING_WEEKS: user_input[CONF_RECYCLING_WEEKS],
                    CONF_RECYCLING_CHORE: user_input[CONF_RECYCLING_CHORE],
                    CONF_CHECK_INTERVAL: int(user_input[CONF_CHECK_INTERVAL]),
                    CONF_ASKS_PER_DAY: int(user_input[CONF_ASKS_PER_DAY]),
                    CONF_ASK_GAP: float(user_input[CONF_ASK_GAP]),
                    CONF_MIN_LABELS: int(user_input[CONF_MIN_LABELS]),
                    CONF_MIN_ACCURACY: int(user_input[CONF_MIN_ACCURACY]),
                    CONF_CONFIDENCE: int(user_input[CONF_CONFIDENCE]),
                    CONF_MIN_ELEVATION: float(user_input[CONF_MIN_ELEVATION]),
                    CONF_MIN_BRIGHTNESS: int(user_input[CONF_MIN_BRIGHTNESS]),
                }
                for key in (CONF_ROI_LEFT, CONF_ROI_TOP, CONF_ROI_RIGHT, CONF_ROI_BOTTOM):
                    changes[key] = float(user_input[key])
                return self._save(changes)
            current = {**current, **user_input}

        def req(key: str, default: Any) -> vol.Required:
            return vol.Required(key, default=current.get(key, default))

        chore_options = [(NO_CHORE, "None")] + [
            (c.key, f"{c.emoji} {c.name}")
            for c in chores_mod.catalogue(
                current.get(CONF_CUSTOM_CHORES, ""), list(current.get(CONF_DISABLED_CHORES) or [])
            )
        ]
        roi_keys = (CONF_ROI_LEFT, CONF_ROI_TOP, CONF_ROI_RIGHT, CONF_ROI_BOTTOM)
        schema_dict: dict = {
            req(CONF_CANS_ENABLED, bool(current.get(CONF_CAMERA)) or DEFAULT_CANS_ENABLED): selector.BooleanSelector(),
            vol.Optional(
                CONF_CAMERA, description={"suggested_value": current.get(CONF_CAMERA) or None}
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="camera")),
            vol.Optional(
                CONF_CANS_CHANNEL_ID,
                description={"suggested_value": current.get(CONF_CANS_CHANNEL_ID, "")},
            ): selector.TextSelector(),
        }
        for key, default in zip(roi_keys, DEFAULT_ROI):
            schema_dict[req(key, default)] = _number(0, 100, 0.5, "%")
        schema_dict.update(
            {
                vol.Optional(
                    CONF_CANS_NIGHTS,
                    default=[str(d) for d in current.get(CONF_CANS_NIGHTS, DEFAULT_CANS_NIGHTS)],
                ): _weekdays(),
                vol.Optional(
                    CONF_RECYCLING_NIGHTS,
                    default=[str(d) for d in current.get(CONF_RECYCLING_NIGHTS, DEFAULT_RECYCLING_NIGHTS)],
                ): _weekdays(),
                req(CONF_RECYCLING_WEEKS, DEFAULT_RECYCLING_WEEKS): _weeks(),
                req(CONF_NAG_TIME, DEFAULT_NAG_TIME): selector.TimeSelector(),
                req(CONF_RENAG, DEFAULT_RENAG): _number(0, MAX_RENAG, 5, "minutes"),
                vol.Optional(
                    CONF_NAG_MENTION, description={"suggested_value": current.get(CONF_NAG_MENTION, "")}
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_BIN_ADMINS, description={"suggested_value": current.get(CONF_BIN_ADMINS, "")}
                ): selector.TextSelector(),
                req(CONF_CANS_CHORE, DEFAULT_CANS_CHORE): _select(chore_options),
                req(CONF_RECYCLING_CHORE, DEFAULT_RECYCLING_CHORE): _select(chore_options),
                req(CONF_CHECK_INTERVAL, DEFAULT_CHECK_INTERVAL): _number(
                    MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL, 5, "minutes"
                ),
                req(CONF_ASKS_PER_DAY, DEFAULT_ASKS_PER_DAY): _number(0, MAX_ASKS_PER_DAY, 1),
                req(CONF_ASK_GAP, DEFAULT_ASK_GAP): _number(0.5, 12, 0.5, "hours"),
                req(CONF_MIN_LABELS, DEFAULT_MIN_LABELS): _number(2, 50, 1),
                req(CONF_MIN_ACCURACY, DEFAULT_MIN_ACCURACY): _number(50, 100, 1, "%"),
                req(CONF_CONFIDENCE, DEFAULT_CONFIDENCE): _number(50, 100, 1, "%"),
                req(CONF_MIN_ELEVATION, DEFAULT_MIN_ELEVATION): _number(-10, 30, 0.5, "°"),
                req(CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS): _number(0, 255, 1),
            }
        )
        return self.async_show_form(step_id="cans", data_schema=vol.Schema(schema_dict), errors=errors)
