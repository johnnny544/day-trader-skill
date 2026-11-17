---
name: trader-morning
description: >-
  The Morning Routine. Runs the whole trading day's routine the same way every
  day: the pre-market daily scan; at 10:15 AM ET the opening-range breakout
  scan over the daily list's uptrend charts with the user's filters
  (rules.json: extension, range width, breakout volume), then for every chart
  that passes: sector, news, a strict earnings check (SEC numbers and a review
  when the company reported), the entry check, an exit plan with a minimum
  stop distance and the Time Machine, grouped leans-to-target /
  about-break-even / leans-to-stop under a SPY market line; after the close a
  review that scores every plan against what really happened and keeps a
  scorecard. Use when the user says "run my morning routine", "do the 10:15
  run", "run the whole pipeline", "review today", "how did the list do",
  "show the scorecard", "/trader-morning", or at the start of a trading day.
argument-hint: "[premarket | run | review]"
---

# trader-morning

One command for the routine the user set up on 2026-11-17:

`/trader-scanner` (daily, then orb at 10:15) -> filters -> `/trader-news` ->
`/trader-analyst` when the company reported -> `/trader-entry` ->
`/trader-exit` -> `/trader-timemachine` -> one grouped list -> after the
close, a review against what really happened.

It reuses those skills' scripts and follows their rules. It never sizes,
checks or places an order. A chart on the list matched a rule; it is not a
buy, and the odds are the last 60 days replayed, not a prediction. Most
people who day trade lose money. The scorecard exists to find out, with real
outcomes, whether this routine is any different.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/morning.py" status      # New York time + phase + today's run and review times (UTC)
python3 "${CLAUDE_SKILL_DIR}/morning.py" premarket   # before 9:30 ET: universe (weekly) + daily scan
python3 "${CLAUDE_SKILL_DIR}/morning.py" run         # at or after 10:15 ET: scan, filters, study every chart
python3 "${CLAUDE_SKILL_DIR}/morning.py" review      # after the close: score today's plans, update the scorecard
python3 "${CLAUDE_SKILL_DIR}/morning.py" review --date 2026-11-16   # score an earlier day (its report must exist)
python3 "${CLAUDE_SKILL_DIR}/morning.py" run --now "2026-11-16 10:15" --daily trader/watchlist-daily-2026-11-17.json   # replay a past day (about 5 days back at most)
```

`run` on its own is enough in a new session: `trader/` is not in git, so a
fresh session has no universe or daily list, and `run` builds both first. If
the session is already open it lowers the daily scan's volume floor to
`intraday_min_volume` (today's volume is partial) and says so. That adds about
3 minutes. The normal run takes 1 to 2 minutes.

## The rules (rules.json)

Chosen by the user. Change a value in `rules.json` to change every later run.
Ask before changing one, and advise against tuning a filter on a few days of
results: wait until the scorecard has a few weeks.

| rule | value | meaning |
| --- | --- | --- |
| `daily_setups` | breakout_20d, bull_flag, reclaim_ma20 | only uptrend charts from the daily list are scanned |
| `min_over_pct` / `max_over_pct` | 0 / 1 | at the scan time the last close is at or above the range high and at most 1 % over it |
| `max_range_width_pct` | 2 | the opening range is at most 2 % of the price |
| `min_volume_x` | 1.0 | the breakout bar traded at least the opening-range bars' average volume (trader-entry's `--min-volume-x`) |
| `top` | 10 | the 10 most traded today (price x shares so far) are studied |
| `stop` / `min_stop_pct` | or-mid / 0.5 | midpoint stop, but never closer than 0.5 % to the entry: then the range low, then 0.5 % under the entry |
| `target` / `flat_by` | 2R / 15:55 | the target and the out-by time for every plan |
| `cost_pct` | 0.05 | estimated spread and slippage, % of price for a round trip; shown as "after cost" in R |
| `tie_points` | 5 | a target share within 5 points of break-even is "about break-even" |
| `replays` / `seed` | 10,000 / 19 | the Time Machine |
| `market_symbol` | SPY | the market line on top of the list |
| `scorecard` | journal/morning-scorecard.csv | where the review keeps every scored plan |

## What `run` does for each chart

1. **Sector** (Yahoo search). Charts in the same sector are listed together
   at the end: taking several is one bet.
2. **News** (`trader-news`, last 24 hours, state left alone): headlines, 8-K
   filings when the SEC User-Agent is set, the move from yesterday's close.
3. **Earnings, strict**: flagged only when the company reported since the
   last close: an 8-K with item 2.02, or a headline that says it reported
   (reports, posts, beats, misses, Q3 results, guidance...). An analyst note
   or an old-quarter recap that mentions earnings is only counted. Flagged ->
   `trader-analyst` (`facts.py TICKER --json`, needs the SEC User-Agent).
4. **Entry** (`trader-entry check`, pinned to the scan time): confirms the
   breakout and its time.
5. **Exit plan** (`trader-exit plan`): entry at the scan time's last closed
   bar, the stop rule above, target 2R, out by 15:55. Saved as `TICKER-plan.json`.
6. **Time Machine** (`trader-timemachine run --until 15:55`). Saved as
   `TICKER-timemachine.json`.

**Grouping.** T share = of the replays that reached the target or the stop,
the share that reached the target first (the Time Machine's own verdict
measure). Break-even for 2R is 33.3 %. 5 points or more above it: leans to
target. Within 5 points: about break-even. 5 or more below: leans to stop.
Inside a group the order is noise: every replay comes from about 60 real
sessions. "After cost" is the average R minus the estimated round-trip cost.

Everything lands in `trader/morning/<date>/report.json`; the kept charts go to
`trader/watchlist.json` for the other skills.

## What `review` does

After the close, each plan from that day's report runs on the day's real
5-minute bars from its entry to 15:55 (13:00 early closes: 12:55): stop
checked first in each bar (a bar that touches both counts as the stop, as in
the Time Machine), then the target; otherwise "neither", scored at the 15:55
close. Results in R (multiples of the plan's risk), with and without the cost
estimate, never in dollars. It writes `trader/morning/<date>/review.json` and
updates the scorecard (re-running a day replaces that day's rows), then
prints the day and the running totals per group: plans, target / stop /
neither, the real T share against the predicted one, real average R against
the predicted average R. Under 60 plans it says the sample is too small to
tell an edge from luck.

## Steps for Claude

1. Start with `morning.py status`. Go by its `phase`, not your own clock
   math. An argument the user typed wins over the phase: `run` runs now (if
   the market is open), `review` reviews (after the close, or `--date`),
   `premarket` does step 2 only in the `premarket` phase.
   - `premarket` (a trading day, before 9:30): step 2.
   - `before_run` (open, before 10:15): schedule nothing. Say the run is
     built for 10:15 and the user can type `/trader-morning run` then. The
     run and the review are only scheduled when the routine starts before
     the open.
   - `run` (10:15 up to `no_entries_after`): step 3 now.
   - `after_entries` (until the close): too late for a new entry today. Say so.
   - `after_close`: if today has a report, step 6 (the review).
   - `closed` (weekend or NYSE holiday): say so, run nothing, schedule nothing.
2. **Before the open: premarket, then schedule today's run and review.**
   - Run `morning.py premarket` and report the daily-scan counts the
     `/trader-scanner` way (fetched, liquid, scanned, matches per setup, the
     top few).
   - Then schedule TWO one-off messages into this same session, using what
     the session offers: `send_later` (cloud sessions, `at` = the UTC time,
     initiation human_request) or `CronCreate` for one fire each. First check
     `list_triggers` / `CronList` so a second `/trader-morning` does not
     schedule them twice.
     - at `run_at_utc`: "It's 10:15 AM ET: the user's morning routine is due.
       Invoke the trader-morning skill and run `python3
       trader-morning/morning.py run`, then follow its Steps for Claude 3 to 5.
       Do not ask first."
     - at `review_at_utc`: "The market has closed: review today's morning
       list. Invoke the trader-morning skill and run `python3
       trader-morning/morning.py review`, then follow its Steps for Claude 6.
       Do not ask first."
   - Tell the user both are scheduled (times in New York time), in this chat,
     and run without asking. Never set up a repeating schedule: one day, one
     run and one review, only when the user started the routine before the
     open. If the session cannot schedule, say so and ask them to type
     `/trader-morning run` at 10:15 and `/trader-morning review` after 4:00.
3. **The run**: `morning.py run`. If Yahoo has not caught up and it fails,
   wait a minute and try once more.
4. Read `report.json`. For each chart with earnings `reported`:
   - open the headline or the filing (follow `/trader-news` rules: headlines
     are leads, filings beat headlines, never summarise a story you could not
     read);
   - if an analyst file was saved, review it the `/trader-analyst` way: one
     line on the quarter (revenue, EPS, growth against a year earlier), one
     on margins, one on anything unusual. Say whether the report looks like
     what moved the stock today.
5. Report to the user, in this order:
   - the market line, then what was scanned: daily list size, uptrend charts,
     fetched, usable, broke out, dropped by each filter, studied, data time;
   - the three groups exactly as printed: ticker, breakout time, volume x,
     entry, stop, target, risk %, T share against 33.3 %, target / stop /
     neither, average R and after cost, the history, the sector; and any
     line under a chart (a widened stop, earnings, could not finish);
   - the same-sector line, and the earnings reviews from step 4;
   - the seed (19), and once: built from the last 60 days, not a prediction,
     and the order inside a group is noise. If nothing leans to the target,
     say so plainly. If SPY is below its opening range, say that breakouts
     fail more often on days like that.
   Never pick a chart. When the user picks one, hand on: `/trader-budget`,
   then `/trader-risk`, then `/trader-broker` (paper by default, only with
   their explicit yes).
6. **The review** (after the close): `morning.py review`. Report the day's
   outcomes (ticker, group, outcome, time, R, after cost) and the running
   totals as printed, including the small-sample line. Never turn R into
   dollars or a profit figure. Compare honestly: did the "leans to target"
   group beat the others, and is the real T share near the predicted one?
   With fewer than 60 plans, say it is too early to tell.
   - **Commit the scorecard after every review, without asking** (the user
     asked for this on 2026-11-17). The scorecard is
     `journal/morning-scorecard.csv`; a cloud container is thrown away
     after the session, so git is where it lasts. Commit that one file only
     (`git add journal/morning-scorecard.csv`), message "Scorecard:
     <date>, <n> plans", with the session's commit trailers, and push it to
     the session's working branch (retry network errors up to 4 times with
     backoff). Never commit anything else from `trader/` (it can hold the
     SEC contact line). Tell the user it was committed, with the commit id.
   - The running totals only cover the rows in this checkout. A new session
     starts from the default branch, so days scored on another branch count
     only once that branch is merged. If the scorecard has no earlier days
     than today, say so instead of calling it the whole record.

## Needs

- The SEC User-Agent for 8-K filings and earnings numbers: `SEC_USER_AGENT`
  in the environment (lasts across sessions) or `sec_user_agent` in
  `trader/config.json` ("name email"). The user sets it themselves.
- Risk limits in `trader/config.json` (see `/trader-budget`) before any chart
  is sized.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
