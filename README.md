# Chores Discord Bot

A Home Assistant custom integration that runs a Discord bot for household
chores. It also watches the curb through a camera you already have in Home
Assistant (a doorbell, say) and learns whether the trash and recycling bins
are out, **entirely on your own Home Assistant**. There is no cloud vision and
no external service, and snapshots never leave your network.

- **Domain:** `chores_discord`
- **Install:** [HACS](https://hacs.xyz/) custom repository
- **Requires:** Home Assistant 2025.3 or newer (runs fine on a Home Assistant Green)
- **Sibling of:** [the laundry bot](https://github.com/keyxmakerx/discord-realtime-bot-ha),
  and built the same way. The bot runs inside Home Assistant and connects out to
  Discord, so it needs no open ports and no separate server.

## The chore board

One card in your channel, edited in place (edits never notify anyone). It
uses Discord's newer card layout: a coloured edge, headings and dividers,
with the controls inside the card.

```
┃ 🧹 Chores                                  (edge: red when anything's overdue)
┃ Needs doing
┃ 🔔 🗑️ Trash · Sam says it needs doing (5m ago)
┃ 🔴 🍽️ Dishes · overdue 1d · done 2d ago by Alex
┃ Claimed
┃ 🟡 ✨ Wipe counters · due in 9h · done 1d ago by Key · 🙋 Alex
┃ Done recently
┃ 🟢 ♻️ Recycling · done 4h ago by 📷 the camera
┃ ─────────────
┃ Quick report: tap what needs doing
┃ [🗑️ Trash] [♻️ Recycling] [🍽️ Dishes] [✨ Counters] [🚽 Bathroom]
┃ [ ✅ I did a chore…        ▾ ]
┃ [ 🙋 I'll take a chore…    ▾ ]
┃ ─────────────
┃ 🗑️ Trash · not out (20m ago) · Tue
┃ ♻️ Recycling · out (20m ago) · Tue (even weeks)
┃ [🔄 Refresh] [📸 Check the bins]
```

| Control | What it does |
|---|---|
| **Quick report** buttons | One tap flags a chore as needing doing now ("Trash needs taking out"), with your name. It jumps to the top of the board until someone marks it done (or after two days). Pick up to five under **Configure → Chores**. |
| **✅ I did a chore…** | Mark a chore done. You get a private **↩️ Undo** for ten minutes. |
| **🙋 I'll take a chore…** | Claim a chore so nobody doubles up. Pick it again to drop the claim. Claims lapse after a day. |
| **📸 Check the bins** | Take a snapshot now and post it asking what you see. |

Once a day (09:00 by default) the board is re-posted at the bottom of the
channel, silently, so it isn't buried under chat.

### Chores

Built in: Dishes (daily), Trash (3 days), Recycling (weekly), Trash-Ext for
boxes and bulky stuff (weekly), Vacuuming (weekly), Mopping (2 weeks),
Bathroom (weekly), Wipe counters (2 days), Clean out fridge (2 weeks).

**Adding chores needs no code.** Under **Configure → Chores**, type one per line:

```
Clean litter box | 🐈 | 2
Water plants | 🪴 | 4 | front room and kitchen
Dishes | 🍽️ | 2
```

The format is `Name | emoji | every N days | note`. Only the name is required,
and `0` days means "whenever needed". A line with the same name as a built-in
replaces it, so the last line above makes Dishes every two days. Built-ins you
don't want can be hidden from the same page. You can have up to 25 chores,
Discord's menu limit.

Automations can tick chores too, for example when the robot vacuum docks:

```yaml
action: chores_discord.mark_done
data:
  chore: vacuuming
  who: Robot
```

## Bin watcher (trash and recycling)

The bins sit too far from the house for a wireless sensor, but the camera can
see them. The bot compares a small box around where they stand to pictures
you've labelled, and learns from every answer. Trash and recycling are learned
separately, from their own answers, so it can tell "trash out, recycling not".

### How it learns

1. **Pick the camera at setup.** Setup lists the cameras Home Assistant
   already has, plus the trash and recycling nights. Recycling can be every
   week or every other week.
2. **Set the region.** Open **Chores** in the Home Assistant sidebar, take a
   snapshot, drag a box around where the bins stand, and save.
3. **Answer a few questions.** In daylight it posts up to 3 snapshots a day
   (at least 2 hours apart) as a card with the cropped photo. Each card has a
   row of buttons per bin: **🗑️ Trash out / Not out**, **♻️ Recycling out /
   Not out**, plus **🤷 Can't tell**. Each answer is used straight away, and
   the card updates to show it and whether the guess was right.
4. **It starts trusting itself** once a bin has at least 5 of each answer
   *and* scores at least 90% predicting its own past answers (a leave-one-out
   self-test). Both thresholds are options. Once trusted, it only asks when
   it's unsure.

### Bin night

At the reminder time (19:00 by default) on a trash or recycling night, it
looks once more, then for each bin due that night:

| What it knows | What it does |
|---|---|
| Trusted, and saw it out in the last 12 hours | Nothing. Ticks the Trash or Recycling chore as done by the camera. |
| Trusted, and it isn't out | One reminder card for every bin still to go out. It pings your chosen mention and whoever claimed the chore, with a **Trash is out** / **Recycling is out** button per bin and **⏭️ No pickup this week**. |
| Still learning, unsure, or dark | The same card, asking "is it out?" instead. With a fresh daylight snapshot, the answer also teaches it. |

If a bin still isn't out an hour later (configurable, 0 turns it off), one
follow-up goes out. Seeing a bin out, or anyone tapping its button, closes it
on the card.

### Daylight only

It only judges the bins when the sun is at least 3° up (from `sun.sun`) and the
region isn't too dark. A night-time look is recorded as "too dark", never as
"not out". In winter the reminder is often after dark, so it relies on the
last daylight look that day, or asks.

## The training page

**Chores** in the Home Assistant sidebar (admins only) is where you work with
the learning directly:

- **Live view:** take a snapshot, see the region drawn on it and the crop the
  model actually compares. Drag a new region and save it.
- **Each bin's guess:** out, not out or unsure, with confidence, the distance
  to the closest labelled sample, and whether it's trusted. You can answer
  right there.
- **Lighting:** sun elevation and region brightness against the thresholds,
  so you can see why it is or isn't judging.
- **Learning:** label counts, self-test score and nights per bin.
- **Waiting for an answer:** snapshots nobody has answered yet. Answering here
  also updates the card in Discord.
- **Labelled samples:** every sample with its photo, label, who and when. The
  ones the model disagrees with are outlined (often a mislabel or a bad
  shot). Flip a label or delete a sample with one click, and the model updates
  immediately. Filter by bin or by "model disagrees".

## What runs, and where

Everything runs on Home Assistant, using only Pillow, which is already part
of Home Assistant core. There is no OpenCV, numpy, model download or network
call. Per look it:

1. Decodes the snapshot at reduced size and crops the region.
2. Computes three small descriptors: a colour histogram, a
   brightness-normalised 16×16 thumbnail and an 8×8 edge map.
3. Compares them to each bin's labelled samples (k-nearest-neighbours).

That takes about 10 ms on a desktop, and a few times that on a Green.
Snapshots are taken every 30 minutes in daylight by default.

Crops of snapshots it asked about are kept under
`/config/chores_discord/samples/` for the training page. A crop is deleted
when no sample uses it any more. Changing the region sets the old samples
aside, since they show a different patch of the picture.
`chores_discord.reset_cans_model` forgets everything.

## Setup

1. **Create the Discord bot.** In the
   [Discord Developer Portal](https://discord.com/developers/applications),
   create an application, open **Bot** and copy the token. No privileged
   intents are needed. Under **OAuth2 → URL Generator**, pick `bot` with
   **View Channels, Send Messages, Embed Links, Attach Files, Read Message
   History, Manage Messages** and invite it. Manage Messages only lets it
   remove its own old board when it re-posts. Turn on Developer Mode,
   right-click the channel and **Copy ID**.
2. **Install.** HACS → ⋮ → **Custom repositories** → add this repository as an
   **Integration**, download **Chores Discord Bot**, restart Home Assistant.
3. **Add it.** **Settings → Devices & services → Add integration → Chores
   Discord Bot**. Step one asks for the token and channel ID. Step two asks
   for the camera (picked from your Home Assistant cameras) and the trash and
   recycling nights. You can skip the camera and add it later.
4. **Tune.** **Configure → Chores and quick reports** and **Configure → Bin
   watcher** hold everything else. The **Chores** sidebar page is the easy way
   to set the region.

You can use the same Discord application as the laundry bot or a separate one.
If both run in the same Home Assistant at once, each needs its own token.

## Entities

| Entity | Description |
|---|---|
| `sensor.chores_overdue` | Number of overdue chores; `chores` attribute lists them. |
| `sensor.chores_trash_bin`, `sensor.chores_recycling_bin` | `out`, `not_out`, `unsure`, `dark` or `unknown`, with guess, confidence, label counts, self-test score and whether it's trusted. Only while the watcher is on. |
| `button.chores_post_board` | Re-post the board at the bottom of the channel. |
| `button.chores_check_bins`, `button.chores_preview_region` | Take a look now; post the region drawn on a snapshot. |

## Actions

`chores_discord.post_board`, `chores_discord.mark_done` (`chore`, `who`),
`chores_discord.check_cans`, `chores_discord.preview_region`,
`chores_discord.reset_cans_model`.

## Tests

Each test file is a standalone script. `test_chores.py`, `test_trash.py` and
`test_vision.py` need only Pillow. `test_flow.py` needs `homeassistant` and
`discord.py`. It drives the whole learn-then-nag loop for both bins against
synthetic street scenes, builds the real Discord cards and checks them
against Discord's component limits.

```sh
for t in tests/test_*.py; do python "$t"; done
```

Design rules are in [`docs/design.md`](docs/design.md).
