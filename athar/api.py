"""Sales from the Odoo BI API instead of the Excel export (targets still come from Excel).

    GET {ODOO_API_URL}?date_from=YYYY-MM-DD HH:MM:SS&date_to=YYYY-MM-DD HH:MM:SS&limit=2000&offset=N
    Authorization: Bearer {ODOO_API_TOKEN}

Answer: {"success": true, "data": [order, ...], "meta": {"total", "limit", "offset", "count", "has_more"}}
with one row per POS order (refunds are negative):
    {"date_order": "2026-08-05 11:35:21", "store_name": "MUBARKIA", "order_name": "MUBARKIA/31329",
     "cashier_name": "5580-AAbbas", "amount_wod_excl_tax": 16.25, "amount_wod_incl_tax": 16.25}
amount = amount_wod_excl_tax (own currency); the orders of one cashier are summed per day and branch.

Time: date_from / date_to / date_order are UTC; the dashboard works in GCC time (ODOO_TZ, default
Asia/Riyadh = UTC+3). The GCC day 2026-08-05 is asked as 2026-08-04 21:00:00 .. 2026-08-05 20:59:59.

A month is far too many orders to download every few minutes, so each finished GCC day is downloaded
once and kept (summed per branch and cashier) in data/api_cache/<api host>/; later runs only fetch today and
yesterday again (yesterday catches tills that sync late).

Settings come from the .env file in the project folder (copy .env.example), or from environment
variables of the same name, which take priority (e.g. GitHub Actions secrets).

Returns what readers.read_pos returns, so the rest of the build is unchanged.
"""
import os
import json
import datetime
import urllib.parse
import urllib.request

from .config import EXCLUDE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, ".env")
CACHE_DIR = os.path.join(ROOT, "data", "api_cache")
TIMEOUT = 180
PAGE = 2000                                          # the API's largest page
REFETCH_DAYS = 2                                     # today + yesterday are always downloaded again
UTC_FMT = "%Y-%m-%d %H:%M:%S"
# GCC zones have no daylight saving, so a fixed offset is exact (and needs no time-zone database).
GCC_OFFSET = {"Asia/Riyadh": 3, "Asia/Kuwait": 3, "Asia/Bahrain": 3, "Asia/Qatar": 3,
              "Asia/Dubai": 4, "Asia/Muscat": 4}


def load_env(path=ENV_FILE):
    """Read KEY=value lines from .env into os.environ. Variables already set (e.g. GitHub secrets) win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            os.environ.setdefault(k, v)


def gcc_offset(tz):
    if tz in GCC_OFFSET:
        return datetime.timedelta(hours=GCC_OFFSET[tz])
    raise ValueError(f"ODOO_TZ={tz} is not a GCC time zone ({', '.join(GCC_OFFSET)})")


def fetch_orders(url, token, utc_from, utc_to):
    """All orders with utc_from <= date_order <= utc_to (UTC datetimes), following the pages."""
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"date_from": utc_from.strftime(UTC_FMT), "date_to": utc_to.strftime(UTC_FMT),
                                    "limit": PAGE, "offset": offset})
        req = urllib.request.Request(f"{url}?{q}", headers={"Authorization": f"Bearer {token}",
                                                             "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = json.load(r)
        if not body.get("success", True) or not isinstance(body.get("data"), list):
            raise ValueError(f"Sales API error: {body.get('message') or body.get('error') or str(body)[:200]}")
        rows = body["data"]
        out += rows
        meta = body.get("meta") or {}
        if not meta.get("has_more") or not rows:
            return out
        offset += len(rows)


def summarise(orders):
    """Orders of one GCC day -> {STORE: {cashier: [excl_tax, incl_tax]}} (refunds net off)."""
    day = {}
    for i, o in enumerate(orders):
        try:
            store = str(o["store_name"]).strip().upper()
            user = str(o.get("cashier_name") or "").strip()
            excl = float(o.get("amount_wod_excl_tax") or 0)
            incl = float(o.get("amount_wod_incl_tax") or 0)
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Sales API order {i} is invalid ({e}): {o}") from None
        if not store or store in EXCLUDE:
            continue
        v = day.setdefault(store, {}).setdefault(user, [0.0, 0.0])
        v[0] += excl
        v[1] += incl
    return {k: {u: [round(a, 2), round(b, 2)] for u, (a, b) in us.items()} for k, us in day.items()}


def day_sales(url, token, day, offset, use_cache):
    """One GCC day, from the cache when allowed, else from the API (and cached when the day is closed).
    Each API host has its own cache folder, so test and live data never mix."""
    folder = os.path.join(CACHE_DIR, urllib.parse.urlparse(url).netloc or "default")
    path = os.path.join(folder, f"{day.isoformat()}.json")
    if use_cache and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    utc_from = datetime.datetime.combine(day, datetime.time()) - offset
    utc_to = utc_from + datetime.timedelta(days=1, seconds=-1)
    orders = fetch_orders(url, token, utc_from, utc_to)
    data = summarise(orders)
    print(f"  {day}: {len(orders)} orders (UTC {utc_from:%m-%d %H:%M} .. {utc_to:%m-%d %H:%M:%S})", flush=True)
    if use_cache and orders:                # an empty day is asked again next time (data may arrive late)
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    return data


def build_sales(days):
    """{date: {STORE: {cashier: [excl, incl]}}} -> (stores, daily) in the shape of readers.read_pos."""
    stores, daily, users = {}, {}, {}
    for d in sorted(days):
        for k, us in days[d].items():
            tot = sum(v[0] for v in us.values())
            daily.setdefault(d, {})[k] = round(tot, 2)
            s = stores.setdefault(k, {"sales": 0.0, "sales_incl": 0.0, "users": []})
            s["sales"] += tot
            s["sales_incl"] += sum(v[1] for v in us.values())
            for u, (a, b) in us.items():
                if u:
                    t = users.setdefault(k, {}).setdefault(u, [0.0, 0.0])
                    t[0] += a
                    t[1] += b
    if not stores:
        raise ValueError(f"The sales API returned no orders for {min(days)} .. {max(days)}")
    for k, s in stores.items():
        s["sales"], s["sales_incl"] = round(s["sales"], 2), round(s["sales_incl"], 2)
        s["users"] = [[u, round(a, 2), round(b, 2)]
                      for u, (a, b) in sorted(users.get(k, {}).items(), key=lambda x: -x[1][0])]
    return stores, {d: v for d, v in daily.items() if v}


def gcc_today():
    """Today's date in the dashboard's GCC time zone (ODOO_TZ)."""
    load_env()
    offset = gcc_offset(os.environ.get("ODOO_TZ") or "Asia/Riyadh")
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + offset).date()


def _env_dates():
    """(from, to) GCC dates set in .env for testing, either may be None; None when neither is set."""
    out = []
    for k in ("SALES_DATE_FROM", "SALES_DATE_TO"):
        v = os.environ.get(k, "").strip()
        try:
            out.append(datetime.date.fromisoformat(v) if v else None)
        except ValueError:
            raise ValueError(f"{k}={v} in .env is not a date like 2026-08-01") from None
    return tuple(out) if any(out) else None


def read_pos_api(asof=None):
    """Month-to-date sales: 1st of the month 00:00 GCC up to now (or to the end of asof, GCC)."""
    load_env()
    url = os.environ.get("ODOO_API_URL", "").strip()
    token = os.environ.get("ODOO_API_TOKEN", "").strip()
    if not url or not token or "your-" in url + token:
        raise ValueError(f"Fill in ODOO_API_URL and ODOO_API_TOKEN in {ENV_FILE}")
    tz = os.environ.get("ODOO_TZ") or "Asia/Riyadh"
    offset = gcc_offset(tz)
    today = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + offset).date()
    end = datetime.date.fromisoformat(asof) if asof else today
    start = end.replace(day=1)
    fixed = _env_dates()                               # testing: SALES_DATE_FROM / SALES_DATE_TO in .env
    if fixed:
        end = fixed[1] or end                          # no SALES_DATE_TO: up to today (or --asof)
        start = fixed[0] or end.replace(day=1)         # no SALES_DATE_FROM: 1st of that month
        if start > end:
            raise ValueError(f"SALES_DATE_FROM {start} is after SALES_DATE_TO {end}")
        print(f"TEST date range from .env: {start} .. {end} (remove SALES_DATE_FROM/TO for live month-to-date)",
              flush=True)
        if (start.year, start.month) != (end.year, end.month):
            print("  note: the range spans more than one month; targets are monthly", flush=True)
    print(f"Sales API: {start} .. {end} ({tz}, sent to the API in UTC)", flush=True)
    days, d = {}, start
    while d <= end:
        recent = (today - d).days < REFETCH_DAYS       # today/yesterday may still change: never cached
        days[d.isoformat()] = day_sales(url, token, d, offset, use_cache=not recent)
        d += datetime.timedelta(days=1)
    return build_sales(days)
