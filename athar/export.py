"""CSV tables matching supabase/schema.sql, for loading the month into a database."""
import os
import csv

from .config import EMP_PATTERN
import re

EMP_RE = re.compile(EMP_PATTERN)


def write_csv(stores, daily, targets, asof, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    month = asof[:7] + "-01"
    with open(os.path.join(out_dir, "targets.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "store", "country", "region", "city", "target_local"])
        for k, t in sorted(targets.items()):
            w.writerow([month, k, t["country"], t["region"], t["city"], t["target"]])
    with open(os.path.join(out_dir, "daily_sales.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sale_date", "store", "sales_local"])
        if daily:
            for d in sorted(daily):
                for k, v in sorted(daily[d].items()):
                    w.writerow([d, k, v])
        else:   # single-total export: one row dated as-of with the month-to-date amount
            for k, s in sorted(stores.items()):
                w.writerow([asof, k, s["sales"]])
    with open(os.path.join(out_dir, "user_sales_mtd.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["asof", "store", "pos_user", "is_employee", "sales_local"])
        for k, s in sorted(stores.items()):
            for u, v in s["users"]:
                w.writerow([asof, k, u, bool(EMP_RE.search(u.strip())), v])
