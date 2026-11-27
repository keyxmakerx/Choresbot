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
    CONF_BOT_TOKEN,
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
    CONF_SAVE_SAMPLES,
    DEFAULT_ASK_GAP,
    DEFAULT_ASKS_PER_DAY,
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
    DEFAULT_SAVE_SAMPLES,
    DOMAIN,
    MAX_ASKS_PER_DAY,
    MAX_CHECK_INTERVAL,
    MAX_RENAG,
    MIN_CHECK_INTERVAL,
    NO_CHORE,
    WEEKDAYS,
)


def _number(minimum: float, maximum: float, step: float, unit: str | None = None):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _select(options: list[tuple[str, str]], *, multiple: bool = False):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=v, label=l) for v, l in options],
            multiple=multiple,
            mode=selector.SelectSelectorMode.LIST if multiple else selector.SelectSelectorMode.DROPDOWN,
        )
    )


class ChoresConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the bot token and the channel; everything else is options."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            channel_id = str(user_input[CONF_CHANNEL_ID]).strip()
            if not channel_id.isdigit():
                errors[CONF_CHANNEL_ID] = "invalid_channel"
            else:
                await self.async_set_unique_id(channel_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Chores Discord Bot",
                    data={CONF_BOT_TOKEN: user_input[CONF_BOT_TOKEN], CONF_CHANNEL_ID: channel_id},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_BOT_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_CHANNEL_ID): selector.TextSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

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
            if problems:
                errors[CONF_CUSTOM_CHORES] = "bad_custom"
                placeholders["problems"] = "; ".join(problems)
            else:
                return self._save(
                    {
                        CONF_CUSTOM_CHORES: user_input.get(CONF_CUSTOM_CHORES, ""),
                        CONF_DISABLED_CHORES: user_input.get(CONF_DISABLED_CHORES, []),
                        CONF_DIGEST: user_input[CONF_DIGEST],
                        CONF_DIGEST_TIME: str(user_input[CONF_DIGEST_TIME]),
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
                vol.Required(CONF_DIGEST, default=current.get(CONF_DIGEST, DEFAULT_DIGEST)): selector.BooleanSelector(),
                vol.Required(
                    CONF_DIGEST_TIME, default=current.get(CONF_DIGEST_TIME, DEFAULT_DIGEST_TIME)
                ): selector.TimeSelector(),
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
                    CONF_CANS_CHORE: user_input[CONF_CANS_CHORE],
                    CONF_CHECK_INTERVAL: int(user_input[CONF_CHECK_INTERVAL]),
                    CONF_ASKS_PER_DAY: int(user_input[CONF_ASKS_PER_DAY]),
                    CONF_ASK_GAP: float(user_input[CONF_ASK_GAP]),
                    CONF_MIN_LABELS: int(user_input[CONF_MIN_LABELS]),
                    CONF_MIN_ACCURACY: int(user_input[CONF_MIN_ACCURACY]),
                    CONF_CONFIDENCE: int(user_input[CONF_CONFIDENCE]),
                    CONF_MIN_ELEVATION: float(user_input[CONF_MIN_ELEVATION]),
                    CONF_MIN_BRIGHTNESS: int(user_input[CONF_MIN_BRIGHTNESS]),
                    CONF_SAVE_SAMPLES: user_input[CONF_SAVE_SAMPLES],
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
            req(CONF_CANS_ENABLED, DEFAULT_CANS_ENABLED): selector.BooleanSelector(),
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
                ): _select([(str(i), name) for i, name in enumerate(WEEKDAYS)], multiple=True),
                req(CONF_NAG_TIME, DEFAULT_NAG_TIME): selector.TimeSelector(),
                req(CONF_RENAG, DEFAULT_RENAG): _number(0, MAX_RENAG, 5, "minutes"),
                vol.Optional(
                    CONF_NAG_MENTION, description={"suggested_value": current.get(CONF_NAG_MENTION, "")}
                ): selector.TextSelector(),
                req(CONF_CANS_CHORE, DEFAULT_CANS_CHORE): _select(chore_options),
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
                req(CONF_SAVE_SAMPLES, DEFAULT_SAVE_SAMPLES): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="cans", data_schema=vol.Schema(schema_dict), errors=errors)
