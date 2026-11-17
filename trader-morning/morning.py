#!/usr/bin/env python3
"""trader-morning: the whole morning routine in one command.

  status     the New York time and the day's phase, plus today's run and review times in UTC
  premarket  universe (if older than a week) + daily scan, and a dated copy of the daily list
  run        at 10:15 ET: opening-range breakout scan over the daily list's uptrend charts, the user's
             filters (rules.json: range, extension, breakout volume), then for every kept chart:
             sector, news, a strict earnings check (SEC numbers when the company reported), the entry
             check, an exit plan with a minimum stop distance, and the Time Machine. Grouped into
             leans-to-target / about-break-even / leans-to-stop, with a SPY market line on top.
  review     after the close: what each listed chart really did by the flat-by time (target, stop
             or neither, in R), kept in a scorecard so the odds can be checked against outcomes.

Reuses the other trader skills' scripts; standard library only. Nothing here sizes, checks or places
an order. Writes trader/morning/<date>/ (one folder a day), trader/watchlist.json (the kept charts) and
the scorecard (rules.json "scorecard").
"""
import argparse, csv, datetime, json, math, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.realpath(__file__))
SKILLS = os.path.dirname(HERE)          # the folder holding trader-scanner, trader-news, ... (symlinks resolved)
ROOT = os.getcwd()                      # like the other skills: trader/ is relative to where you run it
TRADER = os.path.join(ROOT, "trader")
sys.path.insert(0, os.path.join(SKILLS, "trader-scanner"))
import scan as S  # noqa: E402

SCRIPTS = {"scan": "trader-scanner/scan.py", "news": "trader-news/news.py", "facts": "trader-analyst/facts.py",
           "entry": "trader-entry/entry.py", "exit": "trader-exit/exit.py",
           "tm": "trader-timemachine/timemachine.py"}
TIERS = ["leans to target", "about break-even", "leans to stop"]
# A headline that says the company itself reported (not an analyst preview or an old-quarter recap).
REPORTED = re.compile(r"\b(reports?|reported|posts?|posted|beats?|misses|missed|tops|topped|"
                      r"(quarterly|Q[1-4]|fourth[- ]quarter|third[- ]quarter|second[- ]quarter|first[- ]quarter)"
                      r" (results|earnings|profit|revenue|sales)|earnings (beat|miss|call|release)|"
                      r"raises (guidance|outlook)|cuts (guidance|outlook)|guidance)\b", re.I)


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


def prev_trading_day(d):
    d -= datetime.timedelta(days=1)
    while not S.trading_day(d)[0]:
        d -= datetime.timedelta(days=1)
    return d


def utc_iso(et):
    return S.eastern_to_utc(et).strftime("%Y-%m-%dT%H:%M:00Z")


def chart(sym, day):
    """Yahoo v8 5-minute chart that covers `day` (one request)."""
    back = (ny_now(None).date() - day).days
    rng = "1d" if back == 0 else ("5d" if back <= 6 else "60d")
    return S.parse_chart(S.yahoo_get(S.chart_path(sym, rng, "5m")), sym)


def tier_of(share, be, pts):
    if share is None:
        return None
    diff = (share - be) * 100
    return TIERS[0] if diff >= pts else (TIERS[2] if diff <= -pts else TIERS[1])


# ------------------------------------------------------------------ status

def day_times(day, rules):
    s = S.session(day)
    run_at = datetime.datetime.combine(day, S.parse_hhmm(rules["run_at"]))
    close = datetime.datetime.combine(day, s[1]) if s else None
    review_at = close + datetime.timedelta(minutes=rules["review_after_close_min"]) if close else None
    return s, run_at, close, review_at


def cmd_status(a, rules):
    """Where the day stands, so Claude decides premarket / schedule / run / review from the script."""
    now = ny_now(a.now)
    day = now.date()
    ok, why = S.trading_day(day)
    s, run_at, close, review_at = day_times(day, rules)
    cfg = S.read_config(os.path.join(TRADER, "config.json"))
    until = datetime.datetime.combine(day, S.parse_hhmm(cfg.get("no_entries_after") or "15:30", "--until"))
    if not ok:
        phase = "closed"
    elif now.time() < s[0]:
        phase = "premarket"          # before 9:30: premarket, then schedule the run and the review
    elif now < run_at:
        phase = "before_run"         # open, before run_at: nothing is scheduled
    elif now < min(until, close):
        phase = "run"                # run now
    elif now < close:
        phase = "after_entries"      # too late for a new opening-range entry today
    else:
        phase = "after_close"        # the review can run
    daily = os.path.join(TRADER, "watchlist-daily-%s.json" % day.isoformat())
    report = os.path.join(TRADER, "morning", day.isoformat(), "report.json")
    print(json.dumps({
        "now_et": now.strftime("%Y-%m-%d %H:%M"), "weekday": now.strftime("%A"), "trading_day": ok,
        "closed_reason": why or None, "phase": phase, "opens": "09:30",
        "closes": close.strftime("%H:%M") if close else None,
        "run_at_et": run_at.strftime("%Y-%m-%d %H:%M"), "run_at_utc": utc_iso(run_at),
        "review_at_et": review_at.strftime("%Y-%m-%d %H:%M") if review_at else None,
        "review_at_utc": utc_iso(review_at) if review_at else None,
        "no_entries_after": until.strftime("%H:%M"),
        "daily_list": os.path.relpath(daily, ROOT) if os.path.exists(daily) else None,
        "report": os.path.relpath(report, ROOT) if os.path.exists(report) else None}, indent=1))
    return 0


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

def volume_x(bars, k, n_range):
    """The breakout bar's volume over the opening-range bars' average (trader-entry's --min-volume-x)."""
    avg = sum(b["v"] for b in bars[:n_range]) / float(n_range)
    return round(bars[k]["v"] / avg, 2) if avg > 0 else None


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
    n_range = om // S.BAR_MIN
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
    rows, drop = [], {"fell_back": 0, "extended": 0, "wide": 0, "thin": 0}
    for h in hits:
        bars = S.session_bars(cache.get(h["symbol"]) or data[h["symbol"]], day, cutoff)
        last = bars[-1]["c"]
        vol = sum(b["v"] for b in bars)
        over = (last / h["or_high"] - 1) * 100
        width = (h["or_high"] - h["or_low"]) / h["or_high"] * 100
        vx = volume_x(bars, h["bar_index"], n_range)
        if over < rules["min_over_pct"]:
            drop["fell_back"] += 1; continue
        if over > rules["max_over_pct"]:
            drop["extended"] += 1; continue
        if width > rules["max_range_width_pct"]:
            drop["wide"] += 1; continue
        if rules.get("min_volume_x") and (vx is None or vx < rules["min_volume_x"]):
            drop["thin"] += 1; continue
        entry_t = bars[-1]["start"] + datetime.timedelta(minutes=S.BAR_MIN)
        rows.append(dict(h, last=round(last, 2), last_close_at=entry_t.strftime("%H:%M"),
                         over_now=round(over, 2), width_pct=round(width, 2), volume_x=vx,
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


def market_line(rules, day, cutoff, offline=None):
    """Where SPY stands against its own opening range at the scan time. Information only."""
    sym = rules.get("market_symbol") or "SPY"
    try:
        if offline:
            data, _ = S.load_offline(offline)
            series = data.get(sym)
            if not series:
                return {"symbol": sym, "error": "not in the offline file"}
        else:
            series = chart(sym, day)
        n = rules["or_minutes"] // S.BAR_MIN
        bars = S.session_bars(series, day, cutoff)
        if len(bars) <= n or any(b["h"] is None for b in bars[:n]):
            return {"symbol": sym, "error": "not enough bars"}
        oh, ol = max(b["h"] for b in bars[:n]), min(b["l"] for b in bars[:n])
        last = bars[-1]["c"]
        where = "above its opening-range high" if last > oh else ("below its opening-range low" if last < ol
                                                                  else "inside its opening range")
        prev = (series.get("meta") or {}).get("chartPreviousClose") or (series.get("meta") or {}).get("previousClose")
        return {"symbol": sym, "last": round(last, 2), "or_high": round(oh, 2), "or_low": round(ol, 2),
                "where": where, "vs_prev_close_pct": round((last / prev - 1) * 100, 2) if prev else None}
    except Exception as e:  # the market line never stops the run
        return {"symbol": sym, "error": str(e)}


def sector_of(sym):
    try:
        d = json.loads(S.yahoo_get("/v1/finance/search?q=%s&quotesCount=3&newsCount=0" % S.to_yahoo(sym)))
        q = [x for x in d.get("quotes", []) if x.get("symbol") == S.to_yahoo(sym)]
        return (q[0].get("sector") or None, q[0].get("industry") or None) if q else (None, None)
    except Exception:
        return None, None


# ------------------------------------------------------------------ per chart

def earnings_check(items, sym, since_utc):
    """Strict: the company reported since the last close (an 8-K item 2.02, or a headline that says it
    reported). Anything else that mentions earnings is only counted."""
    reported, mentions = [], 0
    for it in items:
        if sym not in (it.get("touches") or [it.get("ticker")]):
            continue
        fresh = (it.get("time") or "") >= since_utc
        blob = json.dumps(it)
        title = it.get("title") or ""
        if it.get("kind") != "headline" and "2.02" in blob and fresh:
            reported.append("8-K item 2.02 (results): " + title[:90])
        elif "earnings" in (it.get("tags") or []):
            if fresh and REPORTED.search(title):
                reported.append("headline: " + title[:90])
            else:
                mentions += 1
    return reported, mentions


def pick_stop(row, rules):
    """The midpoint stop unless it is closer than min_stop_pct; then the range low; then min_stop_pct."""
    entry, oh, ol = row["last"], row["or_high"], row["or_low"]
    floor_pct = rules.get("min_stop_pct") or 0
    mid = math.floor((oh + ol) / 2 * 100) / 100
    mid_pct = (entry - mid) / entry * 100
    if rules["stop"] != "or-mid" or mid_pct >= floor_pct:
        return rules["stop"], None
    low_pct = (entry - ol) / entry * 100
    if low_pct >= floor_pct:
        return "or-low", "midpoint stop only %.2f%% away (under %.1f%%): used the range low" % (mid_pct, floor_pct)
    return "pct:%g" % floor_pct, ("midpoint %.2f%% and range low %.2f%% away (both under %.1f%%): stop %.1f%% "
                                  "under the entry" % (mid_pct, low_pct, floor_pct, floor_pct))


def study(row, day, cutoff, rules, folder, sec_ok, now_text, since_utc):
    sym = row["symbol"]
    out = {"symbol": sym, "scan": row}
    pin = "%s %s" % (day.isoformat(), row["last_close_at"])
    entry_time = S.clock_label(datetime.datetime.strptime(row["last_close_at"], "%H:%M"))
    out["sector"], out["industry"] = sector_of(sym)
    # 1. news
    news, err = tool_json("news", "--tickers", sym, "--json", "--dry-run", "--all", "--hours", rules["news_hours"])
    items = (news or {}).get("items") or []
    out["news"] = {"error": err, "move": next(iter((news or {}).get("moves") or []), None),
                   "items": [{k: i.get(k) for k in ("time", "kind", "title", "tags", "link", "source")}
                             for i in items[:8]],
                   "notes": (news or {}).get("notes")}
    # 2. earnings: only when the company reported since the last close -> the SEC numbers
    reported, mentions = earnings_check(items, sym, since_utc)
    out["earnings"] = {"reported": bool(reported), "why": reported[:3], "other_mentions": mentions}
    if reported:
        if sec_ok:
            facts, ferr = tool_json("facts", sym, "--json")
            if facts:
                json.dump(facts, open(os.path.join(folder, "%s-analyst.json" % sym), "w"), indent=1)
            out["earnings"]["analyst"] = "%s-analyst.json" % sym if facts else None
            out["earnings"]["analyst_error"] = ferr
        else:
            out["earnings"]["analyst_error"] = "skipped: no SEC User-Agent (SEC_USER_AGENT or trader/config.json)"
    # 3. entry check, pinned to the scan time so it agrees with the scan
    ent, err = tool_json("entry", "check", sym, "--json", "--now", pin)
    out["entry"] = {"error": err, "status": (ent or {}).get("status"), "trigger": (ent or {}).get("trigger"),
                    "reason": (ent or {}).get("reason")}
    # 4. exit plan: entered at the scan time's last close, with a minimum stop distance
    stop_rule, stop_why = pick_stop(row, rules)
    plan_path = os.path.join(folder, "%s-plan.json" % sym)
    plan, err = tool_json("exit", "plan", "--symbol", sym, "--entry", row["last"], "--or-high", row["or_high"],
                          "--or-low", row["or_low"], "--date", day.isoformat(), "--time", entry_time,
                          "--stop", stop_rule, "--target", rules["target"], "--flat-by", rules["flat_by"],
                          "--now", pin, "--json", "--write", plan_path)
    if not plan or not os.path.exists(plan_path):
        out["plan"] = {"error": err or "no plan"}
        return out
    out["plan"] = {k: plan.get(k) for k in ("entry", "entry_time", "stop", "stop_rule", "stop_label", "target",
                                            "target_r", "target_label", "risk_per_share", "reward_risk", "flat_by",
                                            "warnings")}
    out["plan"]["stop_why"] = stop_why
    risk = plan.get("risk_per_share") or 0
    out["plan"]["risk_pct"] = round(risk / plan["entry"] * 100, 2) if risk else None
    out["plan"]["cost_r"] = round(plan["entry"] * rules["cost_pct"] / 100 / risk, 3) if risk else None
    # 5. the Time Machine
    tm, err = tool_json("tm", "run", "--plan", plan_path, "--until", rules["flat_by"], "--replays",
                        rules["replays"], "--seed", rules["seed"], "--json",
                        "--write", os.path.join(folder, "%s-timemachine.json" % sym),
                        *(["--now", now_text] if now_text else []))
    if not tm:
        out["tm"] = {"error": err}
        return out
    mc, hist = tm.get("mc") or {}, tm.get("history") or {}
    t = {k: mc.get(k) for k in ("p_target", "p_stop", "p_neither", "p_green", "median_close", "p5_close",
                                "p95_close", "avg_R", "pool_sessions", "seed", "until")}
    t["history"] = {k: hist.get(k) for k in ("sessions", "triggered", "target", "stop", "close", "avg_R")}
    hit = (mc.get("p_target") or 0) + (mc.get("p_stop") or 0)
    t["target_share"] = round(mc["p_target"] / hit, 4) if hit else None
    t["break_even"] = round(1 / (1 + (mc.get("r_at_target") or 1)), 4)
    t["avg_R_after_costs"] = round(mc["avg_R"] - out["plan"]["cost_r"], 3) \
        if mc.get("avg_R") is not None and out["plan"]["cost_r"] is not None else None
    t["tier"] = tier_of(t["target_share"], t["break_even"], rules["tie_points"])
    t["verdict"] = tm.get("verdict")
    t["verdict_notes"] = tm.get("verdict_notes")
    out["tm"] = t
    return out


def pct(x):
    return "  n/a " if x is None else "%5.1f%%" % (x * 100)


def sec_user_agent_set():
    cfg = S.read_config(os.path.join(TRADER, "config.json"))
    ua = str(cfg.get("sec_user_agent") or "")
    return bool(ua and "example.com" not in ua) or bool(os.environ.get("SEC_USER_AGENT"))


def cmd_run(a, rules):
    now = ny_now(a.now)
    daily = a.daily or os.path.join(TRADER, "watchlist-daily-%s.json" % now.date().isoformat())
    if not os.path.exists(daily):
        log("No daily list for %s yet: running the pre-market part first." % now.date().isoformat())
        daily = premarket(rules, a.now)
    day, cutoff, rows, sm = orb_filtered(rules, daily, a.now, a.at, a.offline)
    folder = os.path.join(TRADER, "morning", day.isoformat())
    os.makedirs(folder, exist_ok=True)
    sec_ok = sec_user_agent_set()
    since_utc = utc_iso(datetime.datetime.combine(prev_trading_day(day), S.CLOSE))
    market = market_line(rules, day, cutoff, a.offline)
    results = []
    for i, r in enumerate(rows, 1):
        log("  %d/%d %s: sector, news, earnings, entry, exit, time machine..." % (i, len(rows), r["symbol"]))
        results.append(study(r, day, cutoff, rules, folder, sec_ok, a.now, since_utc))

    def key(x):
        t = x.get("tm") or {}
        tier = TIERS.index(t["tier"]) if t.get("tier") in TIERS else len(TIERS)
        return (tier, -(t.get("target_share") if t.get("target_share") is not None else -1), -(t.get("avg_R") or -9))
    results.sort(key=key)
    sectors = {}
    for x in results:
        if x.get("sector"):
            sectors.setdefault(x["sector"], []).append(x["symbol"])
    clusters = {k: v for k, v in sectors.items() if len(v) > 1}
    report = {"created_by": "trader-morning",
              "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "rules": {k: v for k, v in rules.items() if not k.startswith("_")},
              "daily_list": os.path.relpath(daily, ROOT), "scan": sm, "market": market,
              "sector_clusters": clusters, "sec_user_agent_set": sec_ok, "results": results, "note": S.NOTE}
    with open(os.path.join(folder, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    S.write_json(os.path.join(TRADER, "watchlist.json"),
                 {"created_by": "trader-morning", "mode": "orb", "as_of": "%s %s ET" % (sm["day"], sm["cutoff"]),
                  "note": S.NOTE, "hits": [dict(x["scan"], setups=["orb"] + x["scan"]["daily_setups"])
                                           for x in results]})
    print_report(report, rules, day, cutoff)
    return 0 if results else 1


def print_report(report, rules, day, cutoff):
    sm, results, m = report["scan"], report["results"], report["market"]
    d = sm["dropped"]
    print("trader-morning, %s %s ET. Yahoo 5-minute bars (free, can lag)." % (S.day_label(day), S.clock_label(cutoff)))
    if m.get("error"):
        print("Market: %s line unavailable (%s)." % (m["symbol"], m["error"]))
    else:
        print("Market: %s $%s, %s ($%s to $%s)%s." % (
            m["symbol"], "{:,.2f}".format(m["last"]), m["where"], m["or_low"], m["or_high"],
            "" if m.get("vs_prev_close_pct") is None else ", %+.2f%% vs yesterday's close" % m["vs_prev_close_pct"]))
    print("Daily list %s: %d charts, %d uptrend setups. Fetched %d%s, %d usable, %d candidates, %d confirmed."
          % (os.path.basename(report["daily_list"]), sm["daily_list"], sm["uptrend"], sm["fetched"],
             " (%d failed or had no data)" % sm["failed"] if sm["failed"] else "", sm["usable"], sm["candidates"],
             sm["confirmed"]))
    print("%d broke their opening range. Dropped %d back under the range high, %d more than %.0f%% over, %d range "
          "wider than %.0f%%, %d breakout bar under %.1fx the range's volume. %d left; studied the %d most traded."
          % (sm["broke_out"], d["fell_back"], d["extended"], rules["max_over_pct"], d["wide"],
             rules["max_range_width_pct"], d.get("thin", 0), rules.get("min_volume_x") or 0, sm["passed"], sm["kept"]))
    if not results:
        print("No chart passed every rule. Nothing to study.")
        return
    be = next(((x.get("tm") or {}).get("break_even") for x in results if (x.get("tm") or {}).get("break_even")), None)
    print("Plans: entry at the scan's last close, stop %s (at least %.1f%% away), target %s, out by %s. "
          "Time Machine %s replays, seed %s, last 60 days. Est. costs %.2f%% of price a round trip."
          % (rules["stop"], rules.get("min_stop_pct") or 0, rules["target"], rules["flat_by"],
             "{:,}".format(rules["replays"]), rules["seed"], rules["cost_pct"]))
    print("T share = of the replays that reached the target or the stop, the share that reached the target first. "
          "Break-even %s. Within %g points of it counts as about break-even; inside a group the order is noise."
          % (pct(be).strip(), rules["tie_points"]))
    hdr = ("  ticker  broke out  vol x  entry      stop       target     risk   T share  target  stop    neither  "
           "avg R   after cost  history T/S/close  sector")
    current = None
    for x in results:
        p, t = x.get("plan") or {}, x.get("tm") or {}
        tier = t.get("tier") or "could not finish"
        if tier != current:
            current = tier
            print("%s:" % tier.upper())
            print(hdr)
        h = t.get("history") or {}
        hist = "%s: %s/%s/%s" % (h.get("triggered"), h.get("target"), h.get("stop"), h.get("close")) if h else "n/a"
        trig = ((x.get("entry") or {}).get("trigger") or {}).get("time") or x["scan"].get("at", "-")
        print("  %-6s  %-9s  %-5s  %-9s  %-9s  %-9s  %-5s  %s   %s  %s  %s   %-6s  %-10s  %-17s  %s"
              % (x["symbol"], trig, x["scan"].get("volume_x") or "-", p.get("entry", "-"), p.get("stop", "-"),
                 p.get("target", "-"), "-" if p.get("risk_pct") is None else "%.2f%%" % p["risk_pct"],
                 pct(t.get("target_share")), pct(t.get("p_target")), pct(t.get("p_stop")), pct(t.get("p_neither")),
                 "-" if t.get("avg_R") is None else "%+.2f" % t["avg_R"],
                 "-" if t.get("avg_R_after_costs") is None else "%+.2f" % t["avg_R_after_costs"], hist,
                 x.get("sector") or "-"))
        notes = []
        if p.get("stop_why"):
            notes.append(p["stop_why"])
        if x["earnings"]["reported"]:
            notes.append("EARNINGS: " + "; ".join(x["earnings"]["why"]))
        for e in (p.get("error"), t.get("error")):
            if e:
                notes.append("could not finish: %s" % e)
        for n in notes:
            print("          %s" % n)
    if report["sector_clusters"]:
        print("Same sector (one bet if you take several): " + "; ".join(
            "%s: %s" % (k, ", ".join(v)) for k, v in report["sector_clusters"].items()))
    if not report["sec_user_agent_set"]:
        print("SEC filings and earnings numbers were skipped: no SEC User-Agent (SEC_USER_AGENT or trader/config.json).")
    print("Wrote %s and trader/watchlist.json." % os.path.relpath(
        os.path.join(TRADER, "morning", day.isoformat(), "report.json"), ROOT))
    print("Odds come from the last 60 days of each stock's own bars. They are not a prediction. " + S.NOTE)


# ------------------------------------------------------------------ review (after the close)

FIELDS = ["date", "symbol", "sector", "tier", "target_share", "break_even", "p_target", "p_stop", "p_neither",
          "avg_R_pred", "volume_x", "stop_rule", "entry_time", "entry", "stop", "target", "risk", "target_r",
          "cost_r", "outcome", "exit_time", "R", "R_after_costs"]


def replay_day(bars, entry_time, entry, stop, target, target_r, risk):
    """The plan on the day's real bars after the entry, stop checked first (the Time Machine's rule)."""
    after = [b for b in bars if b["start"].strftime("%H:%M") >= entry_time]
    for b in after:
        end = (b["start"] + datetime.timedelta(minutes=S.BAR_MIN)).strftime("%H:%M")
        if b["l"] is not None and b["l"] <= stop:
            return "stop", end, -1.0
        if b["h"] is not None and b["h"] >= target:
            return "target", end, float(target_r)
    if not after:
        return "no bars", None, None
    last = after[-1]
    return "neither", (last["start"] + datetime.timedelta(minutes=S.BAR_MIN)).strftime("%H:%M"), \
        round((last["c"] - entry) / risk, 3)


def read_scorecard(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def cmd_review(a, rules):
    now = ny_now(a.now)
    day = datetime.date.fromisoformat(a.date) if a.date else now.date()
    folder = os.path.join(TRADER, "morning", day.isoformat())
    path = os.path.join(folder, "report.json")
    if not os.path.exists(path):
        raise S.ScanError("No morning report for %s (%s). Nothing to review." % (day.isoformat(),
                                                                                  os.path.relpath(path, ROOT)))
    report = json.load(open(path))
    flat = datetime.datetime.combine(day, S.parse_hhmm(rules["flat_by"]))
    s = S.session(day)
    if s and datetime.datetime.combine(day, s[1]) < flat:
        flat = datetime.datetime.combine(day, s[1]) - datetime.timedelta(minutes=5)
    if day == now.date() and now < flat + datetime.timedelta(minutes=2):
        raise S.ScanError("It is %s ET: the trades run until %s. Review after that." % (S.clock_label(now),
                                                                                       S.clock_label(flat)))
    rows = []
    for x in report["results"]:
        p, t = x.get("plan") or {}, x.get("tm") or {}
        if not p.get("entry") or not p.get("risk_per_share"):
            continue
        try:
            series = chart(x["symbol"], day)
        except Exception as e:
            log("  %s: no chart (%s)" % (x["symbol"], e))
            continue
        bars = S.session_bars(series, day, flat)
        outcome, end, r = replay_day(bars, x["scan"]["last_close_at"], p["entry"], p["stop"], p["target"],
                                     p.get("target_r") or 2, p["risk_per_share"])
        cost = p.get("cost_r")
        rows.append({"date": day.isoformat(), "symbol": x["symbol"], "sector": x.get("sector") or "",
                     "tier": t.get("tier") or "", "target_share": t.get("target_share"),
                     "break_even": t.get("break_even"), "p_target": t.get("p_target"), "p_stop": t.get("p_stop"),
                     "p_neither": t.get("p_neither"), "avg_R_pred": t.get("avg_R"),
                     "volume_x": x["scan"].get("volume_x"), "stop_rule": p.get("stop_rule"),
                     "entry_time": x["scan"]["last_close_at"], "entry": p["entry"], "stop": p["stop"],
                     "target": p["target"], "risk": p["risk_per_share"], "target_r": p.get("target_r"),
                     "cost_r": cost, "outcome": outcome, "exit_time": end, "R": r,
                     "R_after_costs": None if r is None or cost is None else round(r - cost, 3)})
    json.dump({"date": day.isoformat(), "flat_by": flat.strftime("%H:%M"), "rows": rows},
              open(os.path.join(folder, "review.json"), "w"), indent=1)
    sc_path = os.path.join(ROOT, rules["scorecard"])
    os.makedirs(os.path.dirname(sc_path), exist_ok=True)
    old = [r for r in read_scorecard(sc_path) if r["date"] != day.isoformat()]   # re-running a day replaces it
    allrows = sorted(old + [{k: ("" if v is None else v) for k, v in r.items()} for r in rows],
                     key=lambda r: (r["date"], r["symbol"]))
    with open(sc_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(allrows)
    # ---- print
    print("trader-morning review, %s: each plan on the day's real 5-minute bars until %s ET (Yahoo, can lag). "
          "Stop checked first in a bar that touched both." % (S.day_label(day), S.clock_label(flat)))
    print("  ticker  tier              T share  outcome   at        R       after cost")
    for r in rows:
        print("  %-6s  %-16s  %s   %-8s  %-8s  %-6s  %s" % (
            r["symbol"], r["tier"], pct(r["target_share"]), r["outcome"], r["exit_time"] or "-",
            "-" if r["R"] is None else "%+.2f" % r["R"], "-" if r["R_after_costs"] is None else "%+.2f" % r["R_after_costs"]))
    summarize(allrows, rules)
    print("Scorecard: %s (%d rows). R is in multiples of each plan's risk, not dollars." % (rules["scorecard"],
                                                                                          len(allrows)))
    return 0


def summarize(rows, rules):
    days = sorted({r["date"] for r in rows})
    done = [r for r in rows if r["outcome"] in ("target", "stop", "neither")]
    print("So far: %d plans over %d trading day%s." % (len(done), len(days), "" if len(days) == 1 else "s"))
    groups = [(t, [r for r in done if r["tier"] == t]) for t in TIERS] + [("all", done)]
    print("  group             plans  target  stop  neither  T share real vs predicted  avg R real  after cost  predicted")
    for name, g in groups:
        if not g:
            continue
        tg = sum(1 for r in g if r["outcome"] == "target")
        st = sum(1 for r in g if r["outcome"] == "stop")
        ne = len(g) - tg - st
        share = "%5.1f%%" % (100.0 * tg / (tg + st)) if tg + st else "  n/a "
        pred = [float(r["target_share"]) for r in g if r["target_share"] not in ("", None)]
        avg = lambda k: sum(float(r[k]) for r in g if r[k] not in ("", None)) / max(1, sum(1 for r in g if r[k] not in ("", None)))
        print("  %-16s  %5d  %6d  %4d  %7d  %s vs %5.1f%%           %+6.2f     %+6.2f      %+6.2f"
              % (name, len(g), tg, st, ne, share, 100 * sum(pred) / len(pred) if pred else 0, avg("R"),
                 avg("R_after_costs"), avg("avg_R_pred")))
    if len(done) < 60:
        print("  Fewer than 60 plans: too few to tell a real edge from luck. Keep paper trading and keep scoring.")


# ------------------------------------------------------------------ main

def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["status", "premarket", "run", "review"])
    p.add_argument("--rules", default=os.path.join(HERE, "rules.json"))
    p.add_argument("--daily", help="daily list to scan (default trader/watchlist-daily-<today>.json)")
    p.add_argument("--date", help="review: the day to score, YYYY-MM-DD (default today)")
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
    if a.command == "review":
        return cmd_review(a, rules)
    return cmd_run(a, rules)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except S.ScanError as e:
        print(str(e))
        sys.exit(1)
