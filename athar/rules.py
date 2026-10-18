"""Prize allocation — the single source of truth for who gets what.

Final rules (decided 14 Oct 2026):
  * Qualify = achievement >= 100% (actual at month end; the daily boards use the projected pace).
  * Car (A): company-wide. Highest achievement among qualifiers with >= 300,000 SAR sales.
  * 40 seats by market: UAE 2, Oman 2, Kuwait+Bahrain 2, KSA 34.
      - Small markets: 1st = phone (B), 2nd = gold bar (E).
      - KSA: the remaining 7 phones, 10 iPads, 10 watches, 7 gold bars, in rank order.
  * A small-market seat nobody fills moves to KSA only (next KSA qualifier after the 34).
    Its prize type goes into the KSA pool: the 34 quota holders are upgraded first in rank
    order, and the moved-in branches take the last prizes.
  * Not enough qualifiers -> remaining prizes are dropped, never carried over.
  * One prize per branch.
"""
from .config import MARKET, QUOTA, SMALL_MARKETS, PER_CATEGORY, CAR_MIN_SAR


def allocate(branches):
    """branches: iterable of dicts with keys
         code   – branch code
         country – country from the target file
         pct    – achievement used for ranking (actual or projected), 1.0 = 100%
         sar    – sales in SAR used for the car threshold (actual or projected)
    Returns dict:
         award  {code: "A".."E"}
         car    code or None
         fill   {market: seats filled}
         moved  [codes that got a seat moved from a small market]
    """
    elig = sorted([b for b in branches if b["pct"] >= 1.0], key=lambda b: -b["pct"])
    award = {}
    car = next((b for b in elig if b["sar"] >= CAR_MIN_SAR), None)
    if car:
        award[car["code"]] = "A"
    rest = [b for b in elig if b is not car]
    mkt = lambda b: MARKET.get(b["country"])

    fill, used = {}, {"B": 0, "E": 0}
    small_seats = 0
    for m in SMALL_MARKETS:
        lst = [b for b in rest if mkt(b) == m][:QUOTA[m]]
        fill[m] = len(lst)
        small_seats += len(lst)
        for b, c in zip(lst, ("B", "E")):
            award[b["code"]] = c
            used[c] += 1

    ksa_all = [b for b in rest if mkt(b) == "KSA"]
    ksa = ksa_all[:QUOTA["KSA"]]
    fill["KSA"] = len(ksa)
    vacant = sum(QUOTA[m] for m in SMALL_MARKETS) - small_seats
    moved = ksa_all[QUOTA["KSA"]:QUOTA["KSA"] + vacant]

    pool = ksa + moved                       # quota holders first (already rank-ordered), moved-in last
    seq = (["B"] * (PER_CATEGORY - used["B"]) + ["C"] * PER_CATEGORY +
           ["D"] * PER_CATEGORY + ["E"] * (PER_CATEGORY - used["E"]))
    for b, c in zip(pool, seq):
        award[b["code"]] = c
    return {"award": award, "car": car["code"] if car else None, "fill": fill,
            "moved": [b["code"] for b in moved]}
