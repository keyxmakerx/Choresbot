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
┃ [🔄 Refresh] [👤 Mine] [📸 Check the bins]
```

| Control | What it does |
|---|---|
| **Quick report** buttons | One tap flags a chore as needing doing now ("Trash needs taking out"), with your name. It jumps to the top of the board until someone marks it done (or after two days). Pick up to five under **Configure → Chores**. |
| **✅ I did a chore…** | Mark a chore done. You get a private **↩️ Undo** for ten minutes. |
| **🙋 I'll take a chore…** | Claim a chore so nobody doubles up. Pick it again to drop the claim. Claims lapse after a day. |
| **👤 Mine** | Opens a private panel just for you: what you've claimed, what's up for grabs, and your own settings (below). |
| **📸 Check the bins** | Take a look now and answer only you, privately. Everyone gets a read-only "trash out (92% sure), recycling not out". Admins get the working: the snapshot with the region drawn, the crop, each bin's guess, confidence and closest sample, the lighting, and buttons to teach it. |

Discord can't pin a message to the bottom, so the bot moves the board back
down itself, silently. After 5 messages from people (a setting; 0 turns it
off) it waits for a minute of quiet, so it doesn't jump around mid-conversation,
then re-posts the board at the bottom and removes the old one. It only notices
that someone posted, never what they wrote, so no privileged intent is needed.
It also moves down once a day (09:00 by default). A channel where only the bot
posts keeps the board last without any of this.

### Your own settings

Everyone sets how the bot reaches them from **👤 Mine**. Nobody has to set
anything, and the defaults stay quiet.

| Setting | Choices |
|---|---|
| Bin night reminders | Ping me only if I claimed it (default), always ping me in the channel, send me a DM instead, or never ping me |
| Daily summary | Off (default), or a DM each morning (at the daily board time) with what needs doing |
| When someone flags a chore | Off (default), DM me about any chore, or only about chores I claimed |
| Quiet hours | Off, or a window like 10 PM to 7 AM. In quiet hours nobody mentions you, and DMs arrive without a notification. |

A DM'd bin reminder has the same buttons as the one in the channel, and
tapping one updates both. If someone has DMs from server members turned off,
the bot skips them quietly. The **Who to ping** mention under Bin watcher
still applies to everyone it names.

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
| Trusted, and saw it out in daylight in the last 12 hours | Nothing. Ticks the Trash or Recycling chore as done by the camera. |
| Trusted, and saw it not out in the last hour | One reminder card for every bin still to go out. It pings your chosen mention and whoever claimed the chore, with a **Trash is out** / **Recycling is out** button per bin and **⏭️ No pickup this week**. |
| Dark now, and the last "not out" was earlier | The same card, but it asks instead of insisting: "is it out? It's too dark to see now; at 4:15 PM, my last daylight look, it was not out". |
| Still learning or unsure | The same card, asking "is it out?". With a fresh daylight snapshot, the answer also teaches it. |

If a bin still isn't out an hour later (configurable, 0 turns it off), one
follow-up goes out. It re-checks first, so if it got dark in between, the
follow-up asks rather than repeating "not out". Seeing a bin out, or anyone tapping its button, closes it
on the card.

### Daylight only

It only judges the bins when the sun is at least 3° up (from `sun.sun`) and the
region isn't too dark. A night-time look is recorded as "too dark", never as
"not out", and never hides what the last daylight look saw.

In winter the reminder is often after dark. Then:

- A bin **seen out** in daylight that afternoon counts, and nobody is
  reminded about it.
- A bin last **seen not out** hours earlier probably went out after dark, so
  the reminder asks, and says when it last saw it, instead of claiming it's
  not out.
- Scheduled looks run every 30 minutes until dusk, so the last daylight look
  is usually close to sunset. Putting the bins out before dark lets the
  camera confirm them by itself.

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

## Install on a Home Assistant Green

These steps work on any Home Assistant OS install. The Green needs nothing
extra: Pillow ships with Home Assistant and discord.py installs itself.

### 1. Before you start

- Home Assistant **2025.3 or newer** (**Settings → About**).
- **HACS** installed. Follow the Home Assistant OS guide at
  [hacs.xyz](https://hacs.xyz/docs/use/), which ends with signing HACS in to
  GitHub.
- Your doorbell or curb camera already added to Home Assistant as a `camera.`
  entity. You can skip the camera now and add it later.

### 2. Create the Discord bot (5 minutes)

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   → **New Application**, and name it "Chores".
2. Open **Bot** → **Reset Token**, then copy the token somewhere safe. It's
   shown only once. Leave every privileged intent **off**.
3. Open **OAuth2 → URL Generator**, tick **bot**, then tick these permissions:
   **View Channels, Send Messages, Embed Links, Attach Files, Read Message
   History, Manage Messages**. Open the generated URL and add the bot to your
   server. Manage Messages only lets it remove its own old board when it
   moves the board down.
4. In Discord, **User Settings → Advanced → Developer Mode** on. Right-click
   the chores channel → **Copy Channel ID**. While you're there, right-click
   your own name → **Copy User ID** for step 4 below.

### 3. Install the integration

1. **HACS** → ⋮ (top right) → **Custom repositories**. Paste
   `https://github.com/keyxmakerx/Choresbot`, pick type **Integration** and
   click **Add**.
2. Search HACS for **Chores Discord Bot** → **Download**.
3. **Settings → System → ⏻ (top right) → Restart Home Assistant**.

### 4. Add it

1. **Settings → Devices & services → Add integration → Chores Discord Bot**.
2. Paste the bot token and the channel ID.
3. Pick the camera from the list of your Home Assistant cameras, tick the
   trash night and the recycling night, and say whether recycling is every
   week or every other week. Even and odd follow the ISO week number, so
   check this week's number if pickup alternates.
4. The board appears in the channel within a few seconds.
5. Optional, under **Configure → Bin watcher**: put your user ID in **Who sees
   the details** so 📸 Check the bins shows you the full working. Left
   empty, anyone who can manage the Discord server gets it. **Who to ping**
   takes a user or role mention, like `<@123…>` or `<@&456…>`.

### 5. Point it at the bins

Open **Chores** in the Home Assistant sidebar (admins see it). Click **📸 Take
snapshot**, drag a box snugly around where the bins stand at the curb with a
little margin, and click **Save region**. Saving reloads the integration.

### 6. Teach it for about a week

In daylight it posts up to three snapshots a day. Tap what you see for each
bin. When you put the bins out, tapping 📸 Check the bins and answering
"out" teaches it fastest. Once each bin has 5 of each answer and scores at
least 90% on its self-test, it's trusted and stops asking unless unsure.
The **Chores** page shows how close each bin is.

### Good to know

- **Load on the Green:** one look takes a few tens of milliseconds, every 30
  minutes in daylight. Snapshots never leave the Green.
- **Storage:** chores, settings and labels are kept in Home Assistant's
  storage, and sample crops in `/config/chores_discord/`. Both are included in
  Home Assistant backups.
- **Cloud doorbells:** some only refresh their snapshot on motion. If the
  picture on the **Chores** page looks stale, the bot sees the same stale
  picture. A camera with a live snapshot works best.
- **Changing the token or channel:** **Settings → Devices & services → Chores
  Discord Bot → ⋮ → Reconfigure**.
- **Updates** show up in HACS. Download, then restart Home Assistant.
- **Something wrong?** **Settings → System → Logs**, search for
  `chores_discord`. For more detail, add this to `configuration.yaml` and
  restart:

  ```yaml
  logger:
    logs:
      custom_components.chores_discord: debug
  ```

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
