---
name: trader-morning
description: >-
  The Morning Routine. Runs the whole morning in one go, the same way every
  day: the pre-market daily scan, then at 10:15 AM ET the opening-range
  breakout scan over the daily list's uptrend charts with the user's filters
  (rules.json), then for every chart that passes: the news, an earnings check
  (and the SEC numbers with a short review when earnings are in the news), the
  entry check, an exit plan (opening-range midpoint stop, 2R target) and the
  Time Machine. Lists them most probable first. Use when the user says "run
  my morning routine", "do the 10:15 run", "run the whole pipeline", "scan,
  news, entry and time machine on the hits", "/trader-morning", or at the
  start of a trading day.
argument-hint: "[premarket | run]"
---

# trader-morning

One command for the routine the user set up on 2026-11-17:

`/trader-scanner` (daily, then orb at 10:15) -> filters -> `/trader-news` ->
`/trader-analyst` if earnings are in the news -> `/trader-entry` ->
`/trader-exit` -> `/trader-timemachine` -> one list, most probable first.

It reuses those skills' scripts and follows their rules. It never sizes,
checks or places an order. A chart on the list matched a rule; it is not a
buy, and the odds are the last 60 days replayed, not a prediction. Most
people who day trade lose money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/morning.py" status      # New York time + phase: premarket / before_run / run / after_entries / closed
python3 "${CLAUDE_SKILL_DIR}/morning.py" premarket   # before 9:30 ET: universe (weekly) + daily scan
python3 "${CLAUDE_SKILL_DIR}/morning.py" run         # at or after 10:15 ET: everything else
python3 "${CLAUDE_SKILL_DIR}/morning.py" run --now "2026-11-16 10:15" --daily trader/watchlist-daily-2026-11-17.json   # replay a past day (Yahoo keeps about 5 days of 5-minute bars for the scan)
```

`run` on its own is enough in a new session: `trader/` is not in git, so a
fresh session has no universe or daily list, and `run` builds both first. If
the session is already open it lowers the daily scan's volume floor to
`intraday_min_volume` (today's volume is partial) and says so. That adds about
3 minutes. The normal run takes about 2 minutes for 10 charts.

## The rules (rules.json)

Chosen by the user. Change a value in `rules.json` to change every later run.
Ask before changing one.

| rule | value | meaning |
| --- | --- | --- |
| `daily_setups` | breakout_20d, bull_flag, reclaim_ma20 | only uptrend charts from the daily list are scanned |
| `min_over_pct` / `max_over_pct` | 0 / 1 | at the scan time the last close is at or above the range high and at most 1 % over it (a chart back under it is a failed breakout and is dropped) |
| `max_range_width_pct` | 2 | the opening range is at most 2 % of the price |
| `top` | 10 | the 10 most traded today (price x shares so far) are studied |
| `stop` / `target` / `flat_by` | or-mid / 2R / 15:55 | the exit plan for every chart |
| `replays` / `seed` | 10,000 / 19 | the Time Machine |

## What `run` does for each chart

1. **News** (`trader-news`, last 24 hours, state left alone): headlines, 8-K
   filings when the SEC User-Agent is set, the move from yesterday's close.
2. **Earnings check**: flagged when a headline for that ticker is tagged
   earnings or an 8-K carries item 2.02. Flagged -> `trader-analyst` runs
   `facts.py TICKER --json` and saves `trader/morning/<date>/TICKER-analyst.json`.
   That needs the SEC User-Agent; without it the step is skipped with a note.
3. **Entry** (`trader-entry check`, pinned to the scan time): confirms the
   breakout and gives the bar that first closed above the range high.
4. **Exit plan** (`trader-exit plan`): entry at the scan time's last closed
   bar (the price you could act on now, not the earlier trigger price),
   stop at the opening-range midpoint, target 2R, out by 15:55. Saved as
   `TICKER-plan.json`.
5. **Time Machine** (`trader-timemachine run --until 15:55`): saved as
   `TICKER-timemachine.json`.

**Most probable first** = of the replays that reached the target or the stop,
the share that reached the target first (the Time Machine's own verdict
measure). Break-even for a 2R target is 33.3 %: above it leans to the target,
below it to the stop. Raw "target first" is not used to sort because a tight
stop also gets hit most often. Ties go to the higher average R.

Everything lands in `trader/morning/<date>/report.json`, and the kept charts
go to `trader/watchlist.json` for the other skills.

## Steps for Claude

1. Start with `morning.py status`. It prints the New York time and a
   `phase`. Go by the phase, not your own clock math. The argument the user
   typed wins over the phase: `/trader-morning run` runs now (if the market
   is open), `/trader-morning premarket` does step 2 only when the phase is
   `premarket`.
   - `premarket` (a trading day, before 9:30): step 2.
   - `before_run` (open, before 10:15): schedule nothing. Say the run is
     built for 10:15 and the user can type `/trader-morning run` then.
     The 10:15 run is only scheduled when the routine starts before the open.
   - `run` (10:15 up to `no_entries_after`): step 3 now.
   - `after_entries`: too late for a new opening-range entry today. Say so.
   - `closed` (weekend or NYSE holiday): say so, run nothing, schedule nothing.
2. **Before the open: premarket, then schedule the 10:15 run.**
   - Run `morning.py premarket` and report the daily-scan counts the
     `/trader-scanner` way (fetched, liquid, scanned, matches per setup,
     the top few).
   - Then schedule ONE run for `run_at_utc` from `status` (today's 10:15 ET)
     into this same session, using what the session offers: `send_later`
     (cloud sessions, `at` = `run_at_utc`, initiation human_request) or
     `CronCreate` for one fire. First check nothing is already scheduled for
     today's 10:15 run (`list_triggers` / `CronList`) so a second
     `/trader-morning` does not schedule it twice. The message to send:
     "It's 10:15 AM ET: the user's morning routine is due. Invoke the
     trader-morning skill and run `python3 trader-morning/morning.py run`,
     then follow its Steps for Claude (3 to 6). Do not ask first."
   - Tell the user the run is scheduled for 10:15 AM ET today, in this
     chat, and that it runs without asking. Never set up a repeating
     schedule: one day, one run, only when the user started the routine
     before the open. If the session has no way to schedule, say so and
     ask them to type `/trader-morning run` at 10:15.
3. **The run** (scheduled, or `/trader-morning run`): `morning.py run`. If
   Yahoo has not caught up and it fails, wait a minute and try once more.
4. Read `report.json`. For each chart flagged IN THE NEWS:
   - open the earnings headlines or filing (follow `/trader-news` rules:
     headlines are leads, filings beat headlines, never summarise a story you
     could not read);
   - if an analyst file was saved, review it the `/trader-analyst` way: one
     line on the quarter (revenue, EPS, growth against a year earlier), one
     on margins, one on anything unusual, and whether the next report falls
     inside today's trade. Say if the earnings news looks like it is what
     moved the stock today.
5. Report to the user, in this order:
   - what was scanned: daily list size, uptrend charts, fetched, usable,
     broke out, dropped by each filter, studied, and the data time;
   - the list exactly in the printed order: ticker, breakout time, entry,
     stop, target, target share vs 33.3 % break-even, target / stop /
     neither, average R, the history line, the verdict;
   - under each news-flagged chart, the news and earnings review lines;
   - the seed (19), and once: built from the last 60 days, not a
     prediction. If every chart is close to break-even or leans to the stop,
     say so plainly.
6. Never pick a chart for the user. When they pick one, hand on:
   `/trader-budget` to size it, then `/trader-risk`, then `/trader-broker`
   (paper by default, and only with their explicit yes).

## Needs

- `trader/config.json` with `sec_user_agent` ("Name email") for 8-K filings
  and the earnings numbers. Ask the user for it; never fill it in from
  anything they did not type for this purpose.
- Risk limits in `trader/config.json` (see `/trader-budget`) before any
  chart is sized.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
