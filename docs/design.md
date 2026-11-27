# Design notes

The rules the code enforces and why, one line of rationale each. It follows the
laundry bot's principles: never ping at random, stay quiet when unsure, and
make learning visible and correctable.

## Principles

| # | Principle | Why |
|---|---|---|
| P1 | Fully local | snapshots of the house never leave Home Assistant; only the crop you're asked about is posted to your own Discord channel |
| P2 | Pings only on a fixed schedule | the cans-night reminder and its one follow-up are the only pushes; board edits, daily re-posts and questions are silent |
| P3 | Confidence-gated action | the bot acts on its own guess only once it is trusted (enough labels of each kind *and* a good self-test) and the guess clears the confidence bar; otherwise it asks |
| P4 | Learning is visible | every question shows its guess, every answer says whether the guess was right, the board shows label counts and self-test score, and the labelled crops are on disk |
| P5 | Dark is not "not out" | a look the bot can't see through is recorded as `dark` and never counts against anyone |
| P6 | Additive and reversible | the watcher is off until switched on, and every threshold is an option; reset forgets everything |

## Detection

| Rule | Why |
|---|---|
| Compare only a region of interest | the cans are a small, fixed patch far from the camera; the rest of the frame (cars, people, weather) is noise |
| Three descriptors: hue/sat histogram, normalised 16×16 grey, 8×8 edges | colour finds the cans wherever they stand in the box; the thumbnail and edges catch shape; normalising takes out overall brightness |
| k-NN (k=5, distance-weighted) over the labelled references | the references *are* the model: a new label helps on the very next look, nothing to train, and one bad label is outvoted |
| Leave-one-out self-test over the newest 120 references | measures how well the references separate the two answers, with no separate test set to collect |
| References remember their region and descriptor version | a moved box or a changed descriptor compares different things, so old references are set aside, not mixed in |
| Up to 150 references per label, newest kept | bounds storage and comparison time, and lets the set follow the seasons |
| Daylight gate: sun ≥ 3° and region brightness ≥ 35 | no night vision is assumed; IR footage looks nothing like the daytime references |

## Asking

| Rule | Why |
|---|---|
| While learning: ask up to 3 a day, ≥ 2 h apart | enough to learn in about a week without becoming noise |
| Once trusted: ask only when unsure | the point of learning is to stop asking |
| One extra question on a cans night | "out" examples only exist around pickup |
| 📸 Check the cans always asks | someone tapping it wants to see, and it is the fastest way to teach "out" |
| Answers are one tap and buttons carry the sample id | works across restarts with nothing in memory; an answered or expired (7 days) snapshot says so |

## Cans night

| Situation at reminder time | Action |
|---|---|
| Trusted, seen out within 12 h | silent; tick the cans chore as done by the camera |
| Trusted, seen not out within 12 h | reminder with a ping |
| Learning, unsure, dark, or no recent look | a question; with a daylight snapshot its answer is also a label |
| Nobody answered, still not seen out, after `renag` | one follow-up, never more |
| Seen out, labelled out, or "They're out" tapped | the night is resolved: the reminder is edited, the follow-up cancelled, the chore ticked |

## Chores

| Rule | Why |
|---|---|
| Due when `last_done + every_days` passes; overdue after half an interval or a day, whichever is shorter | a daily chore is overdue by the next evening, a fortnightly one gets a day's grace |
| Claims lapse after 24 h; marking done clears the claim | a forgotten claim must not hide a chore |
| Undo only by whoever marked it | stops accidental or mischievous un-dos |
| Custom chores parse line by line, bad lines reported | a typo can't empty the board |
| No per-person stats on the board | shows who did the last one, never a leaderboard |
