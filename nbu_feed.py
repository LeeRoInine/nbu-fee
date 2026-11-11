#!/usr/bin/env python3
"""Build RSS feeds of National Bank of Ukraine news.

bank.gov.ua publishes no RSS feed. Its news page loads the list with a form POST
to /<lang>/component/source/news/all, so this script asks the same address,
reads each new article once (cached in data/), and writes docs/feed*.xml.
"""
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

BASE = "https://bank.gov.ua"
KYIV = ZoneInfo("Europe/Kyiv")
ROOT = Path(__file__).resolve().parent
OWNER, REPO = "LeeRoInine", "nbu-fee"
RAW = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/main/docs"
UA = f"Mozilla/5.0 (compatible; nbu-feed/1.0; +https://github.com/{OWNER}/{REPO})"

PER_PAGE = 30          # items asked from the list each run
KEEP = 60              # items kept in the feed
NEW_ARTICLES = 12      # article pages read per run (only items not read before)

FEEDS = {
    "ua": {"file": "feed.xml", "title": "НБУ — Новини", "desc": "Новини Національного банку України (bank.gov.ua)"},
    "en": {"file": "feed-en.xml", "title": "NBU — News", "desc": "News of the National Bank of Ukraine (bank.gov.ua)"},
}

UA_MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
             "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
UA_MONTH_NAMES = ["січень", "лютий", "березень", "квітень", "травень", "червень",
                  "липень", "серпень", "вересень", "жовтень", "листопад", "грудень"]
EN_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]


# ---------------------------------------------------------------- network
def http(url, data=None, referer=None, tries=3):
    headers = {"User-Agent": UA, "Accept-Language": "uk,en;q=0.8"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["X-Requested-With"] = "XMLHttpRequest"
    if referer:
        headers["Referer"] = referer
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - report and retry
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


# ---------------------------------------------------------------- parsing
def month_index(word):
    w = word.lower().rstrip(".")
    for names in (UA_MONTHS, UA_MONTH_NAMES, EN_MONTHS):
        if w in names:
            return names.index(w) + 1
    if len(w) >= 3:  # cut short: "черв.", "жовт.", "Jun"
        for names in (UA_MONTHS, EN_MONTHS):
            for i, name in enumerate(names):
                if name.startswith(w):
                    return i + 1
    return 0


def parse_date(text, now=None):
    """'4 черв. 2026 18:00', '29 жовтня 2026', '4 Jun 2026 18:00', 'June 4, 2026 18:00' -> aware UTC datetime."""
    if not text:
        return None
    t = " ".join(text.split())
    now = now or dt.datetime.now(KYIV)
    day = mon = year = None
    m = re.search(r"(\d{1,2})\s+([A-Za-zА-Яа-яІіЇїЄєҐґ]+\.?)(?:,?\s+(\d{4}))?", t)
    if m and month_index(m.group(2)):
        day, mon, year = int(m.group(1)), month_index(m.group(2)), m.group(3)
    else:
        m = re.search(r"([A-Za-z]+\.?)\s+(\d{1,2}),?\s+(\d{4})", t)
        if m and month_index(m.group(1)):
            day, mon, year = int(m.group(2)), month_index(m.group(1)), m.group(3)
    if not day:
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
        if not m:
            return None
        day, mon, year = int(m.group(1)), int(m.group(2)), m.group(3)
    y = int(year) if year else now.year
    hm = re.search(r"(\d{1,2}):(\d{2})", t)
    h, mi = (int(hm.group(1)), int(hm.group(2))) if hm else (12, 0)
    try:
        local = dt.datetime(y, mon, day, h, mi, tzinfo=KYIV)
    except ValueError:
        return None
    if not year and local > now + dt.timedelta(days=1):
        local = local.replace(year=y - 1)
    return local.astimezone(dt.timezone.utc)


def parse_list(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select("div.collection-item"):
        a = item.select_one(".content p a") or item.select_one("a[href]")
        if not a or not a.get("href"):
            continue
        title = a.get_text(" ", strip=True)
        link = urllib.parse.urljoin(BASE, a["href"])
        when = item.select_one(".mark time") or item.select_one("time")
        raw = (when.get("datetime") or when.get_text(" ", strip=True)) if when else ""
        published = parse_date(raw)
        if title:
            out.append({"link": link, "title": title, "date_text": raw,
                        "published": published.isoformat() if published else None})
    return out


def article_html(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    body = soup.select_one(".article-content") or soup.select_one("article") or soup.select_one("main")
    img = soup.select_one('meta[property="og:image"]')
    parts = []
    if img and img.get("content"):
        parts.append(f'<p><img src="{escape(urllib.parse.urljoin(BASE, img["content"]))}" alt=""/></p>')
    if body:
        for bad in body.select("script, style, noscript, iframe, form"):
            bad.decompose()
        for tag in body.find_all(href=True):
            tag["href"] = urllib.parse.urljoin(BASE, tag["href"])
        for tag in body.find_all(src=True):
            tag["src"] = urllib.parse.urljoin(BASE, tag["src"])
        parts.append(str(body))
    return "\n".join(parts)


# ---------------------------------------------------------------- feed
def rfc822(iso):
    return format_datetime(dt.datetime.fromisoformat(iso))


def write_feed(lang, items):
    cfg = FEEDS[lang]
    newest = next((i["published"] or i["first_seen"] for i in items), None)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{escape(cfg['title'])}</title>",
        f"<link>{BASE}/{lang}/news/all</link>",
        f"<description>{escape(cfg['desc'])}</description>",
        f"<language>{'uk' if lang == 'ua' else 'en'}</language>",
        f'<atom:link href="{RAW}/{cfg["file"]}" rel="self" type="application/rss+xml"/>',
    ]
    if newest:
        lines.append(f"<lastBuildDate>{rfc822(newest)}</lastBuildDate>")
    for it in items:
        body = it.get("content") or f"<p>{escape(it['title'])}</p>"
        body = body.replace("]]>", "]]&gt;")
        lines += [
            "<item>",
            f"<title>{escape(it['title'])}</title>",
            f"<link>{escape(it['link'])}</link>",
            f'<guid isPermaLink="true">{escape(it["link"])}</guid>',
            f"<pubDate>{rfc822(it['published'] or it['first_seen'])}</pubDate>",
            f"<description><![CDATA[{body}]]></description>",
            "</item>",
        ]
    lines += ["</channel>", "</rss>", ""]
    (ROOT / "docs" / cfg["file"]).write_text("\n".join(lines), encoding="utf-8")


def update(lang):
    cache_file = ROOT / "data" / f"cache-{lang}.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    listing = http(f"{BASE}/{lang}/component/source/news/all",
                   data={"page": "1", "perPage": str(PER_PAGE)}, referer=f"{BASE}/{lang}/news/all")
    found = parse_list(listing)
    if not found:
        raise RuntimeError(f"{lang}: the list answered but held no items (page layout changed?)")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    for f in found:
        c = cache.setdefault(f["link"], {"first_seen": now})
        c.update({k: v for k, v in f.items() if v is not None})
    reads = 0
    for f in found:  # newest first
        c = cache[f["link"]]
        if "content" in c or reads >= NEW_ARTICLES:
            continue
        reads += 1
        try:
            c["content"] = article_html(http(f["link"]))
        except Exception as e:  # noqa: BLE001 - keep the title-only item, try again next run
            print(f"warning: {f['link']}: {e}", file=sys.stderr)
    items = sorted(({"link": k, **v} for k, v in cache.items()),
                   key=lambda i: i.get("published") or i["first_seen"], reverse=True)[:KEEP]
    cache = {i["link"]: {k: v for k, v in i.items() if k != "link"} for i in items}
    cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    write_feed(lang, items)
    print(f"{lang}: {len(found)} listed, {reads} articles read, {len(items)} in feed")


def main():
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "data").mkdir(exist_ok=True)
    failed = []
    for lang in FEEDS:
        try:
            update(lang)
        except Exception as e:  # noqa: BLE001
            failed.append(lang)
            print(f"error: {e}", file=sys.stderr)
    # The Ukrainian feed is the main one: fail the run (GitHub emails you) if it could not be built.
    if "ua" in failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
