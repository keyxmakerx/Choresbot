"""Constants for the Chores Discord Bot integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "chores_discord"
DEVICE_NAME = "Chores"
PLATFORMS = [Platform.SENSOR, Platform.BUTTON]

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

SIGNAL_UPDATE = f"{DOMAIN}_update"

# --- Config entry data ---
CONF_BOT_TOKEN = "bot_token"
CONF_CHANNEL_ID = "channel_id"

# --- Options: chores ---
CONF_CUSTOM_CHORES = "custom_chores"
CONF_DISABLED_CHORES = "disabled_chores"
CONF_DIGEST_TIME = "digest_time"
CONF_DIGEST = "digest"
CONF_QUICK = "quick_reports"

DEFAULT_CUSTOM_CHORES = ""
DEFAULT_DIGEST_TIME = "09:00:00"
DEFAULT_DIGEST = True

# --- Options: trash cans ---
CONF_CANS_ENABLED = "cans_enabled"
CONF_CAMERA = "camera_entity"
CONF_CANS_CHANNEL_ID = "cans_channel_id"
CONF_ROI_LEFT = "roi_left"
CONF_ROI_TOP = "roi_top"
CONF_ROI_RIGHT = "roi_right"
CONF_ROI_BOTTOM = "roi_bottom"
CONF_CANS_NIGHTS = "cans_nights"
CONF_RECYCLING_NIGHTS = "recycling_nights"
CONF_RECYCLING_WEEKS = "recycling_weeks"
CONF_RECYCLING_CHORE = "recycling_chore"
CONF_NAG_TIME = "nag_time"
CONF_RENAG = "renag_minutes"
CONF_CHECK_INTERVAL = "check_interval"
CONF_ASKS_PER_DAY = "asks_per_day"
CONF_ASK_GAP = "ask_gap_hours"
CONF_MIN_LABELS = "min_labels"
CONF_MIN_ACCURACY = "min_accuracy"
CONF_CONFIDENCE = "confidence"
CONF_MIN_ELEVATION = "min_sun_elevation"
CONF_MIN_BRIGHTNESS = "min_brightness"
CONF_CANS_CHORE = "cans_chore"
CONF_NAG_MENTION = "nag_mention"

DEFAULT_CANS_ENABLED = False
# The region as percentages of the frame: a guess at "far right, lower half".
DEFAULT_ROI = (70.0, 50.0, 95.0, 85.0)
DEFAULT_CANS_NIGHTS: list[str] = []
DEFAULT_RECYCLING_NIGHTS: list[str] = []
DEFAULT_RECYCLING_WEEKS = "every"
DEFAULT_RECYCLING_CHORE = "recycling"
DEFAULT_NAG_TIME = "19:00:00"
DEFAULT_RENAG = 60
DEFAULT_CHECK_INTERVAL = 30
DEFAULT_ASKS_PER_DAY = 3
DEFAULT_ASK_GAP = 2.0
DEFAULT_MIN_LABELS = 5
DEFAULT_MIN_ACCURACY = 90
DEFAULT_CONFIDENCE = 75
DEFAULT_MIN_ELEVATION = 3.0
DEFAULT_MIN_BRIGHTNESS = 35
DEFAULT_CANS_CHORE = "trash"

NO_CHORE = "none"

MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL = 5, 240
MAX_RENAG = 240
MAX_ASKS_PER_DAY = 10

# Crops of every snapshot asked about, for the training page:
# /config/chores_discord/samples/<sid>.jpg. Unreferenced ones are cleaned up.
SAMPLES_DIR = DOMAIN

# The training page in the sidebar.
PANEL_URL = "chores-training"
PANEL_COMPONENT = "chores-training-panel"
PANEL_STATIC = "/chores_discord_static"

# --- Discord custom ids ---
# Every control is a DynamicItem: what it acts on rides in its custom id, so
# taps on any message ever posted route correctly after a restart.
BOARD_SELECT_TEMPLATE = r"chores:board:(?P<what>done|claim)"
BOARD_BUTTON_TEMPLATE = r"chores:board:(?P<what>refresh|check)"
QUICK_TEMPLATE = r"chores:quick:(?P<key>[a-z0-9_]{1,60})"
CANS_LABEL_TEMPLATE = (
    r"chores:cans:(?P<bin>trash|recycling|all):(?P<label>out|in|skip):(?P<sid>[0-9a-f]{1,24})"
)
CANS_NAG_TEMPLATE = (
    r"chores:nag:(?P<bin>trash|recycling|all):(?P<action>out|skip):(?P<night>\d{4}-\d{2}-\d{2})"
)

# --- Services ---
SERVICE_POST_BOARD = "post_board"
SERVICE_MARK_DONE = "mark_done"
SERVICE_CHECK_CANS = "check_cans"
SERVICE_PREVIEW_REGION = "preview_region"
SERVICE_RESET_CANS = "reset_cans_model"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
