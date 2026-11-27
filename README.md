# Chores Discord Bot

A Home Assistant custom integration that runs a Discord bot for household
chores, and watches the curb through your doorbell camera to learn whether the
trash cans are out, **entirely on your own Home Assistant**. No cloud vision,
no external service: snapshots never leave your network.

- **Domain:** `chores_discord`
- **Install:** [HACS](https://hacs.xyz/) custom repository
- **Requires:** Home Assistant 2025.3 or newer (runs fine on a Home Assistant Green)
- **Sibling of:** [the laundry bot](https://github.com/keyxmakerx/discord-realtime-bot-ha),
  built the same way: the bot runs inside Home Assistant and connects out to
  Discord, so it needs no open ports and no separate server.

## The chore board

One message in your channel, edited in place (edits never notify anyone):

```
🧹 Chores
🔴 🍽️ Dishes · overdue 2d · done 3d ago by Sam
🟠 🧹 Vacuuming · due · done 7d ago by Alex
🟡 ✨ Wipe counters · due in 9h · done 1d ago by Sam · 🙋 Alex
🟢 🗑️ Trash · done 4h ago by 📷 the camera
…
🗑️ Trash cans — Last look: not out (20m ago) · Cans night: Tuesday
   Learning: 8 out · 21 not out · self-test 96% · ready
```

| Control | What it does |
|---|---|
| **✅ I did a chore…** | Pick a chore to mark it done. You get a private **↩️ Undo** for ten minutes. |
| **🙋 I'll take a chore…** | Claim a chore so nobody doubles up; pick it again to drop the claim. Claims lapse after a day. |
| **🔄 Refresh** | Redraw the board now (it also refreshes hourly). |
| **📸 Check the cans** | Take a snapshot now and post it asking what you see. |

Once a day (09:00 by default) the board is re-posted at the bottom of the
channel, silently, so it isn't buried under chat.

### Chores

Built in: Dishes (daily), Trash (3 days), Trash-Ext for boxes, recycling and
bulky stuff (weekly), Vacuuming (weekly), Mopping (2 weeks), Bathroom (weekly),
Wipe counters (2 days), Clean out fridge (2 weeks).

**Adding chores needs no code.** Under **Configure → Chores**, type one per line:

```
Clean litter box | 🐈 | 2
Water plants | 🪴 | 4 | front room and kitchen
Dishes | 🍽️ | 2
```

`Name | emoji | every N days | note`. Only the name is required; `0` days means
"whenever needed". A line with the same name as a built-in replaces it (the
last line above makes Dishes every two days). Built-ins you don't want can be
hidden from the same page. Up to 25 chores (Discord's menu limit). To add one
in code instead, append to `DEFAULT_CHORES` in
[`chores.py`](custom_components/chores_discord/chores.py).

Automations can tick chores too, for example when the robot vacuum docks:

```yaml
action: chores_discord.mark_done
data:
  chore: vacuuming
  who: Robot
```

## Trash-can watcher

The cans sit too far from the house for a wireless sensor, but the doorbell can
see them. The bot compares a small box around where the cans stand to pictures
you've labelled, and learns from every answer.

### How it learns

1. **Point it at the curb.** Under **Configure → Trash-can watcher**, turn it
   on, pick the camera, and set the region box (percent of the picture). Press
   **Preview region** (or run `chores_discord.preview_region`) to get a
   snapshot in Discord with the box drawn in red. Make it snug around the spot
   the cans stand, with a little margin.
2. **Answer a few questions.** In daylight it posts up to 3 snapshots a day
   (at least 2 hours apart), cropped to the box, with **🗑️ Cans out**,
   **🏠 Not out** and **🤷 Can't tell**. Each answer is stored as a reference
   and used straight away. Tap **📸 Check the cans** on the board whenever the
   cans are out to give it "out" examples sooner.
3. **It starts trusting itself** once it has at least 5 of each answer *and*
   scores at least 90% predicting its own past answers (a leave-one-out
   self-test). Both thresholds are options. The board shows progress, and
   every reply says whether its guess was right.
4. **Then it goes quiet.** Once trusted, it only asks when it's unsure.

### Cans night

Pick the evening(s) the cans go out and a reminder time (19:00 by default). At
that time it looks once more and:

| What it knows | What it does |
|---|---|
| Trusted, and saw the cans out in the last 12 hours | Nothing. Ticks the **Trash** chore (configurable) as done by the camera. |
| Trusted, and they aren't out | Posts a reminder that pings (your chosen mention, plus whoever claimed Trash), with **✅ They're out** and **⏭️ No pickup this week**. |
| Still learning, unsure, or it's dark | Asks "are the cans out?" instead. With a daylight snapshot, the answer also teaches it. |

If nobody answers and the cans still aren't seen out, one follow-up goes out an
hour later (configurable, 0 turns it off). Seeing the cans out, or anyone
tapping **They're out**, closes that night's reminder.

### Daylight only

It only judges the cans when the sun is at least 3° up (from `sun.sun`) and the
box isn't too dark. A night-time look is recorded as "too dark", never as "not
out". In winter the reminder time is often after dark; it then relies on the
last daylight look that day, or asks.

### What runs, and where

All on Home Assistant, using only Pillow (already part of Home Assistant
core): no OpenCV, numpy, model download or network call. Per look it decodes
the snapshot at reduced size, crops the box and computes three small
descriptors: a colour histogram, a brightness-normalised 16×16 thumbnail and
an 8×8 edge map. It then compares them to every labelled reference
(k-nearest-neighbours). That takes about 10 ms on a desktop, and a few times
that on a Green. Snapshots are taken every 30 minutes in daylight by default.

Labelled crops are also saved under `/config/chores_discord/out` and `/in`
(the newest 200 of each), so you can see exactly what it learned from. Turn
that off with **Keep labelled crops on disk**. Changing the region box sets
the old references aside, since they show a different patch of the picture.
`chores_discord.reset_cans_model` forgets everything.

## Setup

1. **Create the Discord bot.** In the
   [Discord Developer Portal](https://discord.com/developers/applications),
   create an application, open **Bot** and copy the token. No privileged
   intents are needed. Under **OAuth2 → URL Generator**, pick `bot` with
   **View Channels, Send Messages, Embed Links, Attach Files, Read Message
   History, Manage Messages** and invite it. (Manage Messages lets it remove
   its own old board when it re-posts; it never touches anyone else's.) Turn on
   Developer Mode, right-click the channel and **Copy ID**.
2. **Install.** HACS → ⋮ → **Custom repositories** → add this repository as an
   **Integration**, download **Chores Discord Bot**, restart Home Assistant.
3. **Add it.** **Settings → Devices & services → Add integration → Chores
   Discord Bot**, then enter the token and channel ID. The board appears in the
   channel.
4. **Configure.** **Configure → Chores** for the chore list, **Configure →
   Trash-can watcher** for the cans.

You can use the same Discord application as the laundry bot or a separate one.
Each integration needs its own token if both are running at once in the same
Home Assistant.

## Entities

| Entity | Description |
|---|---|
| `sensor.chores_overdue` | Number of overdue chores; `chores` attribute lists them. |
| `sensor.chores_trash_cans` | `out`, `not_out`, `unsure`, `dark` or `unknown`, with the guess, confidence, label counts, self-test score and whether it's trusted. Only while the watcher is on. |
| `button.chores_post_board` | Re-post the board at the bottom of the channel. |
| `button.chores_check_cans`, `button.chores_preview_region` | Take a look now; draw the region on a snapshot. |

## Actions

`chores_discord.post_board`, `chores_discord.mark_done` (`chore`, `who`),
`chores_discord.check_cans`, `chores_discord.preview_region`,
`chores_discord.reset_cans_model`.

## Tests

Each test file is a standalone script. `test_chores.py`, `test_trash.py` and
`test_vision.py` need only Pillow. `test_flow.py` drives the whole
learn-then-nag loop against synthetic street scenes and needs
`homeassistant` and `discord.py`:

```sh
for t in tests/test_*.py; do python "$t"; done
```

Design rules are in [`docs/design.md`](docs/design.md).
