"""Turn the parsed inputs into the JSON each page embeds.

internal_data(...) -> data for templates/internal.html   (has amounts — management only)
public_data(...)   -> data for templates/public.html     (percentages + names only, NO amounts)
"""
import re
import json
import calendar
import datetime

from .config import FX, MARKET, CAR_MIN_SAR, EMP_PATTERN
from .rules import allocate

EMP_RE = re.compile(EMP_PATTERN)


# ---------------------------------------------------------------- internal board
def internal_data(stores, daily, targets, asof, elapsed=None):
    """Same shape the internal page expects: {raw:{asof,stores,prev?,prevAsof?}, targets, targetMonth, daily?}.
    The page itself computes everything in the browser (so its upload button gives identical results)."""
    data = {"raw": {"asof": asof, "stores": stores}, "targets": targets, "targetMonth": asof[:7]}
    if elapsed:                        # live data: asof is today and not over yet (see public_data)
        data["raw"]["elapsed"] = elapsed
    if daily and len(daily) > 1:
        ds = sorted(daily)
        prev = {}
        for d in ds[:-1]:
            for k, v in daily[d].items():
                prev[k] = round(prev.get(k, 0) + v, 2)
        data["raw"]["prev"] = prev
        data["raw"]["prevAsof"] = ds[-2]
    if daily:
        data["daily"] = daily          # lets the page draw the month trend without any database
    return data


# ---------------------------------------------------------------- public board
def pretty(u):
    """'1900-SHussein' -> 'SHussein (1900)'"""
    u = u.strip()
    m = re.match(r"^(\d{3,})\s*-\s*(.+)$", u) or re.match(r"^(.+?)\s*-\s*(\d{3,})$", u)
    if not m:
        return u
    num, name = (m.group(1), m.group(2)) if m.group(1).isdigit() else (m.group(2), m.group(1))
    return f"{name} ({num})"


def public_data(stores, daily, targets, asof, elapsed=None):
    """elapsed: finished days of the month, when the data includes today while today is still running
    (live API). Today's sales then count towards achievement, but the pace and projections divide by the
    finished days only, so a half-done day does not pull every projection down. None = asof is a full day."""
    if daily:
        days = sorted(daily)
    else:                                   # single-total layout: no per-day history available
        days = [asof]
        daily = {asof: {k: v["sales"] for k, v in stores.items()}}
    asof = days[-1]
    d0 = datetime.date.fromisoformat(asof)
    dim = calendar.monthrange(d0.year, d0.month)[1]
    de = elapsed or d0.day
    frac = de / dim

    # cumulative local-currency sales per day — used here only, never exported
    cum_by_day, cum = {}, {}
    for d in days:
        for k, v in daily[d].items():
            cum[k] = cum.get(k, 0) + v
        cum_by_day[d] = dict(cum)

    def ach(d, k):
        t = targets.get(k)
        return (cum_by_day[d].get(k, 0) / t["target"]) if t and t["target"] else 0

    def ranking(d):
        ks = sorted([k for k in cum_by_day[d] if k in targets], key=lambda k: -ach(d, k))
        return {k: i + 1 for i, k in enumerate(ks)}

    prev = days[-2] if len(days) > 1 else asof
    rank_now, rank_prev = ranking(asof), ranking(prev)

    def awards_for(d):
        fr = (de if d == asof else int(d[-2:])) / dim
        rows = [{"code": k, "country": targets[k]["country"], "pct": ach(d, k) / fr,
                 "sar": cum_by_day[d][k] * FX[targets[k]["country"]] / fr}
                for k in cum_by_day[d] if k in targets]
        return allocate(rows)

    A = awards_for(asof)
    award, car, fill, moved = A["award"], A["car"], A["fill"], set(A["moved"])
    award_prev = awards_for(prev)["award"] if prev != asof else dict(award)

    def projected(k):
        return ach(asof, k) / frac if frac else 0

    def top_names(k, n=3):
        us = [u for u in stores.get(k, {}).get("users", []) if EMP_RE.search(u[0].strip())]
        us.sort(key=lambda u: -u[1])
        return [pretty(u[0]) for u in us[:n]]

    def best_day(k):                         # best single day as points of target (a ratio, not money)
        t = targets[k]["target"]
        if not t or len(days) < 2:
            return {"bd": 0, "bdd": None}
        v, dd = max((daily[d].get(k, 0) / t, int(d[-2:])) for d in days)
        return {"bd": round(v, 4), "bdd": dd}

    branches = []
    for k in rank_now:
        t = targets[k]
        a, ap = ach(asof, k), ach(prev, k)
        branches.append({"s": k, "c": t["country"], "city": t["city"], "a": round(a, 4), "d": round(a - ap, 4),
                         "p": round(projected(k), 3), "r": rank_now[k], "rp": rank_prev.get(k, rank_now[k]),
                         "aw": award.get(k), "mv": k in moved, "top": top_names(k), **best_day(k)})
    branches.sort(key=lambda b: b["r"])

    mk = {}
    for k, t in targets.items():
        m = mk.setdefault(t["country"], {"s": 0, "t": 0, "n": 0, "on": 0, "at100": 0})
        m["s"] += cum_by_day[asof].get(k, 0); m["t"] += t["target"]; m["n"] += 1
        m["on"] += ach(asof, k) >= frac
        m["at100"] += ach(asof, k) >= 1
    markets = {c: {"a": round(m["s"] / m["t"], 4), "n": m["n"], "on": m["on"], "at100": m["at100"]} for c, m in mk.items()}

    tot_t_sar = sum(t["target"] * FX[t["country"]] for t in targets.values())
    trend = []
    for d in days:
        s_sar = sum(v * FX[targets[k]["country"]] for k, v in cum_by_day[d].items() if k in targets)
        byc = {c: round(sum(v for k, v in cum_by_day[d].items() if k in targets and targets[k]["country"] == c) / mk[c]["t"], 4)
               for c in markets}
        trend.append({"d": int(d[-2:]), "a": round(s_sar / tot_t_sar, 4), "c": byc})

    ORDER = "ABCDE"
    news = []
    for k in sorted(set(award) | set(award_prev), key=lambda k: rank_now.get(k, 999)):
        a, b = award.get(k), award_prev.get(k)
        if a == b:
            continue
        kind = "in" if a and not b else "out" if b and not a else ("up" if ORDER.index(a) < ORDER.index(b) else "down")
        top = top_names(k, 1)
        news.append({"k": kind, "s": k, "c": targets[k]["country"], "u": top[0] if top else None,
                     "aw": a, "was": b, "a": round(ach(asof, k), 4)})
    news.sort(key=lambda n: ["in", "up", "down", "out"].index(n["k"]))

    sar_proj = lambda k: cum_by_day[asof][k] * FX[targets[k]["country"]] / frac
    car_list = sorted([k for k in rank_now if sar_proj(k) >= CAR_MIN_SAR], key=lambda k: -projected(k))[:5]

    data = {"asof": asof, "prev": prev, "news": news, "de": de, "day": d0.day, "dim": dim, "frac": round(frac, 4),
            "company": {"a": trend[-1]["a"], "on": sum(1 for b in branches if b["a"] >= frac),
                        "at100": sum(1 for b in branches if b["a"] >= 1), "n": len(branches)},
            "markets": markets, "fill": fill, "moved": len(moved), "branches": branches, "trend": trend,
            "car": [{"s": k, "c": targets[k]["country"], "a": round(ach(asof, k), 4), "p": round(projected(k), 3),
                     "top": top_names(k)} for k in car_list]}
    blob = json.dumps(data, ensure_ascii=False)
    assert "sales" not in blob and "target" not in blob, "public data must never contain amounts"
    return data


# ---------------------------------------------------------------- summary for the console / log
def summary(stores, targets, asof, elapsed=None):
    d0 = datetime.date.fromisoformat(asof)
    dim = calendar.monthrange(d0.year, d0.month)[1]
    frac = (elapsed or d0.day) / dim
    rows, tot_s, tot_t = [], 0, 0
    for k, s in stores.items():
        t = targets.get(k)
        if not t:
            continue
        fx = FX[t["country"]]
        tot_s += s["sales"] * fx; tot_t += t["target"] * fx
        a = s["sales"] / t["target"] if t["target"] else 0
        rows.append({"code": k, "country": t["country"], "pct": a / frac, "sar": s["sales"] * fx / frac, "a": a})
    A = allocate(rows)
    return {"asof": asof, "total_sar": round(tot_s), "achievement": tot_s / tot_t, "expected": frac,
            "on_track": sum(1 for r in rows if r["pct"] >= 1), "car": A["car"], "fill": A["fill"],
            "moved": A["moved"], "unmatched": sorted(k for k in stores if k not in targets),
            "no_sales": sorted(k for k in targets if k not in stores)}
