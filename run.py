#!/usr/bin/env python3
"""Daily build of both Athar dashboards.

    python run.py --pos data/pos.xlsx --target data/target_2026-10.xlsx
    python run.py --pos data/pos.xlsx --target data/target_2026-10.xlsx --asof 2026-10-13   # old single-total export
    python run.py --api --target data/target.xlsx      # sales from the Odoo BI API (settings in .env)
    python run.py --api --target data/target.xlsx --asof 2026-10-13   # API, month-to-date up to that day
    python run.py --api --target data/target.xlsx --every     # keep running: rebuild from Odoo every 5 minutes
    python run.py --api --target data/target.xlsx --serve     # open http://localhost:8000 — "Refresh now" button

Outputs (folder --out, default site/):
    index.html      public board «سباق أثر»  — percentages and names only, safe to share with branches
    internal.html   management board «لوحة أثر» — contains sales amounts, do NOT publish openly
    data/public.json, data/internal.json, data/summary.json
Add --csv to also write CSV tables for importing into a database (e.g. Supabase).
"""
import os
import sys
import json
import argparse
import time
import threading
import http.server

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from athar.readers import read_pos, read_targets          # noqa: E402
from athar.api import read_pos_api, gcc_today            # noqa: E402
from athar.build import internal_data, public_data, summary  # noqa: E402
from athar.render import render                          # noqa: E402

REFRESH_MINUTES = 5          # --every with no number: rebuild from Odoo every 5 minutes
SERVE_PORT = 8000            # --serve with no number


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pos", help="Odoo POS pivot export (.xlsx)")
    src.add_argument("--api", action="store_true", help="read sales from the Odoo BI API (URL and token in .env) instead")
    ap.add_argument("--target", required=True, help="monthly target file (.xlsx)")
    ap.add_argument("--asof", help="last day in the data (YYYY-MM-DD); needed for the single-total export, "
                                   "optional with --api (default: today)")
    ap.add_argument("--out", default=os.path.join(ROOT, "site"))
    ap.add_argument("--csv", action="store_true", help="also write CSV tables for a database")
    ap.add_argument("--public-only", action="store_true", help="skip internal.html (for public hosting)")
    ap.add_argument("--every", type=float, nargs="?", const=REFRESH_MINUTES, metavar="MINUTES",
                    help=f"with --api: keep running and rebuild every MINUTES (default {REFRESH_MINUTES:g}); "
                         "pages then reload themselves")
    ap.add_argument("--serve", type=int, nargs="?", const=SERVE_PORT, metavar="PORT",
                    help=f"with --api: show the pages on http://localhost:PORT (default {SERVE_PORT}) with a "
                         "'Refresh now' button that pulls the API again; can be combined with --every")
    ap.add_argument("--reload", type=float, metavar="MINUTES",
                    help="one-off build: open pages reload themselves every MINUTES (e.g. for GitHub Pages)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="with --serve: 0.0.0.0 lets other PCs on the network open it (internal.html has amounts!)")
    a = ap.parse_args()

    if a.serve is not None:
        if not a.api:
            sys.exit("--serve only works with --api")
        serve(a)
        return
    if a.every is None:
        build(a, refresh=int(a.reload * 60) if a.reload else None)
        return
    if not a.api:
        sys.exit("--every only works with --api (an Excel file does not change by itself)")
    if a.every < 1:
        sys.exit("--every must be at least 1 minute")
    if a.asof:
        sys.exit("--every always reads up to today; leave out --asof")
    secs = int(a.every * 60)
    print(f"Rebuilding every {a.every:g} min — press Ctrl+C to stop")
    try:
        while True:
            started = time.time()
            print(time.strftime("[%Y-%m-%d %H:%M:%S]"), flush=True)
            try:
                build(a, refresh=secs)
            except (Exception, SystemExit) as e:      # keep the last good pages and try again next round
                print("Build failed, keeping the previous pages:", e, flush=True)
            time.sleep(max(0, secs - (time.time() - started)))
    except KeyboardInterrupt:
        print("Stopped.")

# "Refresh now" button, added to the pages by --serve as it sends them (the files on disk stay unchanged).
# It sits in the page's own header bar and uses the page's own pill style.
REFRESH_BUTTON = """<script>(function(){
var bar=document.querySelector('header.head .status, header.head .meta'); if(!bar) return;
var b=document.createElement('button'); b.type='button'; b.className='pill'; b.id='btnRefreshNow';
b.style.cssText='cursor:pointer;font:inherit;font-weight:700';
var idle='🔄 تحديث الآن'; b.textContent=idle; bar.insertBefore(b, bar.firstChild);
b.title='سحب أحدث المبيعات من النظام الآن';
b.addEventListener('click',function(){
  b.disabled=true; b.textContent='⏳ جارٍ التحديث…';
  fetch('api/refresh',{method:'POST'}).then(function(res){return res.json().then(function(j){return [res.status,j];});})
  .then(function(x){
    if(x[1].ok){ location.reload(); return; }
    b.textContent = x[0]===409 ? '⏳ تحديث آخر جارٍ… حاول بعد قليل' : '⚠️ تعذّر التحديث';
    if(x[1].error && x[0]!==409) b.title=x[1].error;
    setTimeout(function(){b.disabled=false;b.textContent=idle;},6000);
  }).catch(function(){ b.textContent='⚠️ تعذّر الاتصال'; setTimeout(function(){b.disabled=false;b.textContent=idle;},6000); });
});
})();</script>"""


class Refresher:
    """One build at a time, whether started by the timer or by the 'Refresh now' button."""

    def __init__(self, a, secs):
        self.a, self.secs, self.lock = a, secs, threading.Lock()
        self.last, self.error = None, None

    def run(self, who):
        if not self.lock.acquire(blocking=False):
            return False                                # a refresh is already running
        try:
            print(time.strftime("[%Y-%m-%d %H:%M:%S]"), who, flush=True)
            try:
                build(self.a, refresh=self.secs)
                self.last, self.error = time.strftime("%Y-%m-%d %H:%M:%S"), None
            except (Exception, SystemExit) as e:       # keep the last good pages
                self.error = str(e)
                print("Build failed, keeping the previous pages:", e, flush=True)
            return True
        finally:
            self.lock.release()


def serve(a):
    """Serve the --out folder and answer GET /api/status, POST /api/refresh for the pages' button."""
    secs = int(a.every * 60) if a.every else None
    if a.every is not None and (a.every < 1 or a.asof):
        sys.exit("--every needs at least 1 minute and no --asof")
    r = Refresher(a, secs)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=a.out, **kw)

        def _json(self, code, data):
            b = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/status":
                return self._json(200, {"ok": True, "busy": r.lock.locked(), "last": r.last, "error": r.error})
            page = {"/": "index.html", "/index.html": "index.html", "/internal.html": "internal.html"}.get(path)
            if page and os.path.exists(os.path.join(a.out, page)):
                html = open(os.path.join(a.out, page), encoding="utf-8").read()
                b = html.replace("</body>", REFRESH_BUTTON + "</body>", 1).encode()   # button only when served here
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return self.wfile.write(b)
            super().do_GET()

        def do_POST(self):
            if self.path.split("?")[0] != "/api/refresh":
                return self._json(404, {"ok": False})
            if not r.run("Refresh now (button)"):
                return self._json(409, {"ok": False, "error": "busy"})
            self._json(200 if not r.error else 500, {"ok": not r.error, "last": r.last, "error": r.error})

        def log_message(self, *args):
            pass

    os.makedirs(a.out, exist_ok=True)
    if secs:                                            # refresh now, then every `secs`, in the background
        def loop():
            while True:
                started = time.time()
                r.run(f"every {a.every:g} min")
                time.sleep(max(0, secs - (time.time() - started)))
        threading.Thread(target=loop, daemon=True).start()
    elif not os.path.exists(os.path.join(a.out, "index.html")):
        r.run("first build")
    srv = http.server.ThreadingHTTPServer((a.host, a.serve), Handler)
    host = "localhost" if a.host in ("127.0.0.1", "0.0.0.0") else a.host
    print(f"Dashboards: http://{host}:{a.serve}/  (public)   http://{host}:{a.serve}/internal.html  (internal)")
    print("'Refresh now' button pulls the API again" + (f"; also every {a.every:g} min" if secs else
          "; no automatic refresh") + " — press Ctrl+C to stop", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


def build(a, refresh=None):
    if a.api:
        try:
            stores, daily = read_pos_api(a.asof)
        except Exception as e:
            sys.exit(f"Sales API failed: {e}")
    else:
        stores, daily = read_pos(a.pos)
    targets = read_targets(a.target)
    elapsed = None
    if a.api:
        asof = max(daily)                               # last day that actually has sales
        if asof == gcc_today().isoformat():             # today is still running: count finished days only
            elapsed = max(1, int(asof[-2:]) - 1)
            print(f"Today ({asof}) is still running: its sales count, the pace uses {elapsed} finished days",
                  flush=True)
    elif daily:
        last = max(daily)
        if a.asof and a.asof != last:
            sys.exit(f"--asof {a.asof} does not match the last day column in the file ({last})")
        asof = last
    elif a.asof:
        asof = a.asof
    else:
        sys.exit("This export has no day columns. Pass --asof YYYY-MM-DD (the last day it covers), "
                 "or export from Odoo with days as columns.")

    os.makedirs(os.path.join(a.out, "data"), exist_ok=True)
    pub = public_data(stores, daily, targets, asof, elapsed)
    pub["built"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())   # shown as "last updated" (GCC time)
    pub["tz"] = os.environ.get("ODOO_TZ") or "Asia/Riyadh"
    render(os.path.join(ROOT, "templates", "public.html"), pub, os.path.join(a.out, "index.html"), refresh)
    json.dump(pub, open(os.path.join(a.out, "data", "public.json"), "w", encoding="utf-8"), ensure_ascii=False)
    if not a.public_only:
        inn = internal_data(stores, daily, targets, asof, elapsed)
        render(os.path.join(ROOT, "templates", "internal.html"), inn, os.path.join(a.out, "internal.html"), refresh)
        json.dump(inn, open(os.path.join(a.out, "data", "internal.json"), "w", encoding="utf-8"), ensure_ascii=False)

    s = summary(stores, targets, asof, elapsed)
    json.dump(s, open(os.path.join(a.out, "data", "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.csv:
        from athar.export import write_csv
        write_csv(stores, daily, targets, asof, os.path.join(a.out, "csv"))

    print(f"Data through {asof}: {s['total_sar'] / 1e6:.2f}M SAR, achievement {s['achievement']:.1%} "
          f"vs expected {s['expected']:.1%}, on track {s['on_track']}")
    print(f"Car leader (projected): {s['car']} | seats filled {s['fill']} | moved to KSA {s['moved']}")
    if s["unmatched"]:
        print("WARNING branches with sales but no target:", s["unmatched"])
    print("Written to", a.out)


if __name__ == "__main__":
    main()
