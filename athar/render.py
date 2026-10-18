"""Inject JSON into a page template and wrap it as a complete, self-contained web page."""
import os
import re
import json

_EMBED = re.compile(r'(<script id="embed" type="application/json">)(.*?)(</script>)', re.S)


# Reloads the page so it picks up the file the next rebuild writes. Skipped while the internal page's
# upload panel is open or after a manual upload there (a reload would throw that data away).
_RELOAD = ("<script>setInterval(function(){var p=document.getElementById('updatePanel');"
           "if(!window.atharNoReload&&!(p&&!p.hidden))location.reload();},%d);</script>")


def render(template_path, data, out_path, refresh=None):
    """refresh = seconds between automatic page reloads (None = never)."""
    page = open(template_path, encoding="utf-8").read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page, n = _EMBED.subn(lambda m: m.group(1) + blob + m.group(3), page, count=1)
    if n != 1:
        raise ValueError(f"{template_path}: embed <script> block not found")
    title = re.search(r"<title>(.*?)</title>", page)
    t = title.group(1) if title else "Athar"
    body = re.sub(r"<title>.*?</title>", "", page, count=1)
    html = ('<!doctype html>\n<html lang="ar" dir="rtl"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{t}</title></head><body>\n{body}\n"
            + (_RELOAD % (refresh * 1000) if refresh else "") + "</body></html>\n")
    tmp = out_path + ".tmp"                 # write then swap, so a reload never sees a half-written page
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, out_path)
    return len(html)
