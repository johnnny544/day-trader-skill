#!/usr/bin/env python3
"""trader-morning: the whole morning routine in one command.

  premarket  universe (if older than a week) + daily scan, and a dated copy of the daily list
  run        at 10:15 ET: opening-range breakout scan over the daily list's uptrend charts, the user's
             filters (rules.json), then for every kept chart: news, an earnings check (and the SEC
             numbers when earnings are in the news), the entry check, an exit plan and the Time
             Machine. Sorted by how often the target came first in the replays.

Reuses the other trader skills' scripts; standard library only. Nothing here sizes, checks or places
an order. Writes trader/morning/<date>/ (one folder a day) and trader/watchlist.json (the kept charts).
"""
import argparse, datetime, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.realpath(__file__))
SKILLS = os.path.dirname(HERE)          # the folder holding trader-scanner, trader-news, ... (symlinks resolved)
ROOT = os.getcwd()                      # like the other skills: trader/ is relative to where you run it
TRADER = os.path.join(ROOT, "trader")
sys.path.insert(0, os.path.join(SKILLS, "trader-scanner"))
import scan as S  # noqa: E402

SCRIPTS = {"scan": "trader-scanner/scan.py", "news": "trader-news/news.py", "facts": "trader-analyst/facts.py",
           "entry": "trader-entry/entry.py", "exit": "trader-exit/exit.py",
           "tm": "trader-timemachine/timemachine.py"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def tool(name, *args, timeout=900):
    """Run another skill's script from the repo root. -> (exit code, stdout, stderr)."""
    cmd = [sys.executable, os.path.join(SKILLS, SCRIPTS[name])] + [str(x) for x in args]
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ds" % timeout
    return p.returncode, p.stdout, p.stderr


def tool_json(name, *args):
    code, out, err = tool(name, *args)
    try:
        return json.loads(out), None
    except ValueError:
        return None, (out.strip() or err.strip() or "exit %d, no output" % code).splitlines()[-1]


def load_rules(path):
    with open(path) as fh:
        return json.load(fh)


def ny_now(text):
    return S.to_eastern(S.parse_now(text))


def in_session(now):
    s = S.session(now.date())
    return bool(s) and s[0] <= now.time() < s[1]


# ------------------------------------------------------------------ premarket

def premarket(rules, now_text=None, intraday=None):
    now = ny_now(now_text)
    uni = os.path.join(TRADER, "universe.txt")
    age = (time.time() - os.path.getmtime(uni)) / 86400 if os.path.exists(uni) else None
    if age is None or age > rules["universe_max_age_days"]:
        log("Universe %s: rebuilding from Nasdaq Trader..." % ("missing" if age is None else "%.0f days old" % age))
        code, out, err = tool("scan", "universe")
        log(out.strip() or err.strip())
        if code:
            raise S.ScanError("could not build the universe")
    intraday = in_session(now) if intraday is None else intraday
    args = ["daily", "--show", "0"]
    if intraday:
        args += ["--min-volume", rules["intraday_min_volume"]]
        log("The session is open: today's volume is partial, so the daily scan's volume floor is %s shares "
            "instead of 1,000,000." % "{:,}".format(rules["intraday_min_volume"]))
    log("Daily scan of the universe (about 2 to 3 minutes)...")
    code, out, err = tool("scan", *args)
    print(out.strip())
    if code and "Wrote" not in out:
        raise S.ScanError("the daily scan found nothing or could not run: " + (out.strip() or err.strip()))
    dst = os.path.join(TRADER, "watchlist-daily-%s.json" % now.date().isoformat())
    shutil.copyfile(os.path.join(TRADER, "watchlist.json"), dst)
    print("Saved the daily list as %s." % os.path.relpath(dst, ROOT))
    return dst


# ------------------------------------------------------------------ the 10:15 scan

def orb_filtered(rules, daily_path, now_text, at_text, offline=None):
    daily = json.load(open(daily_path))
    tags = {h["symbol"]: h.get("setups") or [] for h in daily["hits"]}
    want = set(rules["daily_setups"])
    symbols = [s for s, t in tags.items() if want & set(t)]
    now = S.parse_now(now_text)
    at = S.parse_hhmm(at_text) if at_text else None
    cfg = S.read_config(os.path.join(TRADER, "config.json"))
    until = S.parse_hhmm(cfg.get("no_entries_after") or "15:30", "--until")
    om = rules["or_minutes"]
    cache = {}
    if offline:
        data, failed = S.load_offline(offline)
        symbols = [s for s in symbols if s in data or s in failed]
        day, cutoff = S.orb_window(now, at, om, until)
        confirm = None
    else:
        day, cutoff = S.orb_window(now, at, om, until)
        rng = "1d" if day == ny_now(None).date() else "5d"
        log("Opening-range scan: 5-minute closes for %d uptrend charts..." % len(symbols))
        data, failed = S.fetch_spark(symbols, rng, "5m", S.PAUSE)

        def confirm(sym):
            time.sleep(S.PAUSE)
            s = S.parse_chart(S.yahoo_get(S.chart_path(sym, rng, "5m")), sym)
            cache[sym] = s
            return s

    hits, counts, failed2 = S.scan_orb(data, symbols, day, cutoff, om, rules["min_price"], 0, confirm, 0)
    failed = dict(failed or {}); failed.update(failed2)
    rows, drop = [], {"fell_back": 0, "extended": 0, "wide": 0}
    for h in hits:
        bars = S.session_bars(cache.get(h["symbol"]) or data[h["symbol"]], day, cutoff)
        last = bars[-1]["c"]
        vol = sum(b["v"] for b in bars)
        over = (last / h["or_high"] - 1) * 100
        width = (h["or_high"] - h["or_low"]) / h["or_high"] * 100
        if over < rules["min_over_pct"]:
            drop["fell_back"] += 1; continue
        if over > rules["max_over_pct"]:
            drop["extended"] += 1; continue
        if width > rules["max_range_width_pct"]:
            drop["wide"] += 1; continue
        entry_t = bars[-1]["start"] + datetime.timedelta(minutes=S.BAR_MIN)
        rows.append(dict(h, last=round(last, 2), last_close_at=entry_t.strftime("%H:%M"),
                         over_now=round(over, 2), width_pct=round(width, 2),
                         dollar_volume=round(last * vol), daily_setups=tags[h["symbol"]]))
    passed = len(rows)
    rows.sort(key=lambda r: -r["dollar_volume"])
    rows = rows[:rules["top"]]
    summary = {"day": day.isoformat(), "cutoff": cutoff.strftime("%H:%M"), "daily_list": len(tags),
               "uptrend": len(symbols), "fetched": counts["fetched"],
               "failed": len([s for s in symbols if s in failed]), "usable": counts["scanned"],
               "candidates": counts["candidates"], "confirmed": counts["confirmed"], "broke_out": len(hits),
               "dropped": drop, "passed": passed, "kept": len(rows)}
    return day, cutoff, rows, summary


# ------------------------------------------------------------------ per chart

def earnings_flag(items, sym):
    why = []
    for it in items:
        if sym not in (it.get("touches") or [it.get("ticker")]):
            continue
        blob = json.dumps(it)
        if it.get("kind") != "headline" and "2.02" in blob:
            why.append("8-K item 2.02 (earnings results): " + (it.get("title") or "")[:90])
        elif "earnings" in (it.get("tags") or []):
            why.append("headline: " + (it.get("title") or "")[:90])
    return why


def study(row, day, cutoff, rules, folder, sec_ok, now_text):
    sym = row["symbol"]
    out = {"symbol": sym, "scan": row}
    pin = "%s %s" % (day.isoformat(), row["last_close_at"])
    entry_time = S.clock_label(datetime.datetime.strptime(row["last_close_at"], "%H:%M"))
    # 1. news
    news, err = tool_json("news", "--tickers", sym, "--json", "--dry-run", "--all", "--hours", rules["news_hours"])
    items = (news or {}).get("items") or []
    out["news"] = {"error": err, "move": next(iter((news or {}).get("moves") or []), None),
                   "items": [{k: i.get(k) for k in ("time", "kind", "title", "tags", "link", "source")}
                             for i in items[:8]],
                   "notes": (news or {}).get("notes")}
    # 2. earnings -> the SEC numbers
    flag = earnings_flag(items, sym)
    out["earnings"] = {"in_news": bool(flag), "why": flag[:3]}
    if flag:
        if sec_ok:
            facts, ferr = tool_json("facts", sym, "--json")
            json.dump(facts, open(os.path.join(folder, "%s-analyst.json" % sym), "w"), indent=1)
            out["earnings"]["analyst"] = "%s-analyst.json" % sym if facts else None
            out["earnings"]["analyst_error"] = ferr
        else:
            out["earnings"]["analyst_error"] = "skipped: no SEC User-Agent set (sec_user_agent in trader/config.json)"
    # 3. entry check, pinned to the scan time so it agrees with the scan
    ent, err = tool_json("entry", "check", sym, "--json", "--now", pin)
    out["entry"] = {"error": err, "status": (ent or {}).get("status"), "trigger": (ent or {}).get("trigger"),
                    "reason": (ent or {}).get("reason")}
    # 4. exit plan: entered at the scan time, at the last closed bar's close
    plan_path = os.path.join(folder, "%s-plan.json" % sym)
    plan, err = tool_json("exit", "plan", "--symbol", sym, "--entry", row["last"], "--or-high", row["or_high"],
                          "--or-low", row["or_low"], "--date", day.isoformat(), "--time", entry_time,
                          "--stop", rules["stop"], "--target", rules["target"], "--flat-by", rules["flat_by"],
                          "--now", pin, "--json", "--write", plan_path)
    if not plan or not os.path.exists(plan_path):
        out["plan"] = {"error": err or "no plan"}
        return out
    out["plan"] = {k: plan.get(k) for k in ("entry", "entry_time", "stop", "stop_label", "target", "target_label",
                                            "risk_per_share", "reward_risk", "flat_by", "warnings")}
    # 5. the Time Machine
    tm, err = tool_json("tm", "run", "--plan", plan_path, "--until", rules["flat_by"], "--replays",
                        rules["replays"], "--seed", rules["seed"], "--json",
                        "--write", os.path.join(folder, "%s-timemachine.json" % sym),
                        *(["--now", now_text] if now_text else []))
    if not tm:
        out["tm"] = {"error": err}
        return out
    mc, hist = tm.get("mc") or {}, tm.get("history") or {}
    out["tm"] = {k: mc.get(k) for k in ("p_target", "p_stop", "p_neither", "p_green", "median_close", "p5_close",
                                        "p95_close", "avg_R", "pool_sessions", "seed", "until")}
    out["tm"]["history"] = {k: hist.get(k) for k in ("sessions", "triggered", "target", "stop", "close", "avg_R")}
    hit = (mc.get("p_target") or 0) + (mc.get("p_stop") or 0)
    out["tm"]["target_share"] = round(mc["p_target"] / hit, 4) if hit else None
    out["tm"]["break_even"] = round(1 / (1 + (mc.get("r_at_target") or 1)), 4)
    out["tm"]["verdict"] = tm.get("verdict")
    out["tm"]["verdict_notes"] = tm.get("verdict_notes")
    return out


def pct(x):
    return "  n/a " if x is None else "%5.1f%%" % (x * 100)


def cmd_run(a, rules):
    now = ny_now(a.now)
    daily = a.daily or os.path.join(TRADER, "watchlist-daily-%s.json" % now.date().isoformat())
    if not os.path.exists(daily):
        log("No daily list for %s yet: running the pre-market part first." % now.date().isoformat())
        daily = premarket(rules, a.now)
    at = a.at or (None if a.now else None)
    day, cutoff, rows, sm = orb_filtered(rules, daily, a.now, at, a.offline)
    folder = os.path.join(TRADER, "morning", day.isoformat())
    os.makedirs(folder, exist_ok=True)
    cfg = S.read_config(os.path.join(TRADER, "config.json"))
    sec_ok = bool(cfg.get("sec_user_agent") and "example.com" not in cfg.get("sec_user_agent", "")) \
        or bool(os.environ.get("SEC_USER_AGENT"))
    results = []
    for i, r in enumerate(rows, 1):
        log("  %d/%d %s: news, earnings, entry, exit, time machine..." % (i, len(rows), r["symbol"]))
        results.append(study(r, day, cutoff, rules, folder, sec_ok, a.now))
    # Most probable first: of the replays that hit the target or the stop, how often the target came first
    # (the Time Machine's own verdict measure). Raw "target first" would reward tight stops that also get hit most.
    def key(x):
        t = x.get("tm") or {}
        return (-(t.get("target_share") if t.get("target_share") is not None else -1), -(t.get("avg_R") or -9))
    results.sort(key=key)
    report = {"created_by": "trader-morning", "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "rules": {k: v for k, v in rules.items() if not k.startswith("_")}, "daily_list": os.path.relpath(daily, ROOT),
              "scan": sm, "sec_user_agent_set": sec_ok, "results": results, "note": S.NOTE}
    with open(os.path.join(folder, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    S.write_json(os.path.join(TRADER, "watchlist.json"),
                 {"created_by": "trader-morning", "mode": "orb", "as_of": "%s %s ET" % (sm["day"], sm["cutoff"]),
                  "note": S.NOTE, "hits": [dict(x["scan"], setups=["orb"] + x["scan"]["daily_setups"])
                                           for x in results]})
    # ---- print
    d = sm["dropped"]
    print("trader-morning, %s %s ET. Yahoo 5-minute bars (free, can lag)." % (S.day_label(day), S.clock_label(cutoff)))
    print("Daily list %s: %d charts, %d uptrend setups. Fetched %d%s, %d usable, %d candidates, %d confirmed."
          % (os.path.basename(daily), sm["daily_list"], sm["uptrend"], sm["fetched"],
             " (%d failed or had no data)" % sm["failed"] if sm["failed"] else "", sm["usable"], sm["candidates"],
             sm["confirmed"]))
    print("%d broke their opening range. Dropped %d back under the range high, %d more than %.0f%% over, %d range "
          "wider than %.0f%%. %d left; studied the %d most traded."
          % (sm["broke_out"], d["fell_back"], d["extended"], rules["max_over_pct"], d["wide"],
             rules["max_range_width_pct"], sm["passed"], sm["kept"]))
    if not results:
        print("No chart passed every rule. Nothing to study.")
        return 1
    print("Plans: entry at the %s close, stop %s, target %s, out by %s. Time Machine %s replays, seed %s, "
          "last 60 days." % (S.clock_label(cutoff), rules["stop"], rules["target"], rules["flat_by"],
                             "{:,}".format(rules["replays"]), rules["seed"]))
    print("Most probable first: of the replays that reached the target or the stop, the share that reached the target "
          "first (break-even %s for a %s target)." % (pct((results[0].get("tm") or {}).get("break_even")).strip(),
                                                        rules["target"]))
    print("  #  ticker  broke out  entry      stop       target     T share  target  stop    neither  avg R   "
          "history (fired: T/S/close)  earnings")
    for n, x in enumerate(results, 1):
        p, t = x.get("plan") or {}, x.get("tm") or {}
        h = t.get("history") or {}
        hist = "%s: %s/%s/%s" % (h.get("triggered"), h.get("target"), h.get("stop"), h.get("close")) if h else "n/a"
        trig = ((x.get("entry") or {}).get("trigger") or {}).get("time") or x["scan"].get("at", "-")
        print("  %-2d %-6s  %-9s  %-9s  %-9s  %-9s  %s   %s  %s  %s   %-6s  %-26s  %s"
              % (n, x["symbol"], trig, p.get("entry", "-"), p.get("stop", "-"), p.get("target", "-"),
                 pct(t.get("target_share")), pct(t.get("p_target")), pct(t.get("p_stop")), pct(t.get("p_neither")),
                 "-" if t.get("avg_R") is None else "%+.2f" % t["avg_R"], hist,
                 "IN THE NEWS" if x["earnings"]["in_news"] else "-"))
        if t.get("verdict"):
            print("       %s" % t["verdict"])
        for e in (p.get("error"), t.get("error")):
            if e:
                print("       could not finish: %s" % e)
    if not sec_ok:
        print("SEC filings and earnings numbers were skipped: no SEC User-Agent (sec_user_agent in trader/config.json).")
    print("Wrote %s and trader/watchlist.json." % os.path.relpath(os.path.join(folder, "report.json"), ROOT))
    print("Odds come from the last 60 days of each stock's own bars. They are not a prediction. " + S.NOTE)
    return 0


def cmd_status(a, rules):
    """Where the day stands, so Claude decides premarket / schedule / run from the script, not by hand."""
    now = ny_now(a.now)
    day = now.date()
    ok, why = S.trading_day(day)
    s = S.session(day)
    run_at = datetime.datetime.combine(day, S.parse_hhmm(rules["run_at"]))
    cfg = S.read_config(os.path.join(TRADER, "config.json"))
    until = datetime.datetime.combine(day, S.parse_hhmm(cfg.get("no_entries_after") or "15:30", "--until"))
    if not ok:
        phase = "closed"
    elif now.time() < s[0]:
        phase = "premarket"          # before 9:30: run premarket, then schedule the run_at run
    elif now < run_at:
        phase = "before_run"         # open, but before run_at: nothing is scheduled
    elif now < until and now.time() < s[1]:
        phase = "run"                # run now
    else:
        phase = "after_entries"      # too late for a new opening-range entry today
    daily = os.path.join(TRADER, "watchlist-daily-%s.json" % day.isoformat())
    print(json.dumps({
        "now_et": now.strftime("%Y-%m-%d %H:%M"), "weekday": now.strftime("%A"), "trading_day": ok,
        "closed_reason": why or None, "phase": phase, "opens": "09:30", "run_at_et": run_at.strftime("%Y-%m-%d %H:%M"),
        "run_at_utc": S.eastern_to_utc(run_at).strftime("%Y-%m-%dT%H:%M:00Z"),
        "no_entries_after": until.strftime("%H:%M"),
        "daily_list": os.path.relpath(daily, ROOT) if os.path.exists(daily) else None}, indent=1))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["status", "premarket", "run"])
    p.add_argument("--rules", default=os.path.join(HERE, "rules.json"))
    p.add_argument("--daily", help="daily list to scan (default trader/watchlist-daily-<today>.json)")
    p.add_argument("--now", help='pin the clock, New York time, e.g. "2026-11-16 10:15"')
    p.add_argument("--at", help="cut the scan at this New York time (replay a day already done)")
    p.add_argument("--offline", help="saved Yahoo 5-minute charts for the scan (tests)")
    a = p.parse_args()
    rules = load_rules(a.rules)
    if a.command == "status":
        return cmd_status(a, rules)
    if a.command == "premarket":
        premarket(rules, a.now)
        return 0
    return cmd_run(a, rules)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except S.ScanError as e:
        print(str(e))
        sys.exit(1)
