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

# Research events (https://bank.gov.ua/ua/research/events?category=N): the page lists them itself.
EVENT_FEEDS = {
    "seminars": {"category": 3, "file": "feed-seminars.xml", "title": "НБУ — Відкриті дослідницькі семінари",
                 "desc": "Відкриті дослідницькі семінари НБУ (bank.gov.ua/ua/research/events?category=3)"},
    "workshops": {"category": 5, "file": "feed-workshops.xml", "title": "НБУ — Практичні воркшопи",
                  "desc": "Практичні воркшопи НБУ (bank.gov.ua/ua/research/events?category=5)"},
}
EVENT_PAGES = 2        # list pages read per category each run (5 events a page)
EVENT_REREAD_DAYS = 14 # an event page is read again (materials, video) until this long after the event

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


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def write_feed(cfg, items, page_link, language):
    newest = next((i["pub"] for i in items), None)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{escape(cfg['title'])}</title>",
        f"<link>{escape(page_link)}</link>",
        f"<description>{escape(cfg['desc'])}</description>",
        f"<language>{language}</language>",
        f'<atom:link href="{RAW}/{cfg["file"]}" rel="self" type="application/rss+xml"/>',
    ]
    if newest:
        lines.append(f"<lastBuildDate>{rfc822(newest)}</lastBuildDate>")
    for it in items:
        body = it.get("content") or f"<p>{escape(it['title'])}</p>"
        body = body.replace("]]>", "]]&gt;")
        lines += [
            "<item>",
            f"<title>{escape(it['feed_title'])}</title>",
            f"<link>{escape(it['link'])}</link>",
            f'<guid isPermaLink="true">{escape(it["link"])}</guid>',
            f"<pubDate>{rfc822(it['pub'])}</pubDate>",
            f"<description><![CDATA[{body}]]></description>",
            "</item>",
        ]
    lines += ["</channel>", "</rss>", ""]
    (ROOT / "docs" / cfg["file"]).write_text("\n".join(lines), encoding="utf-8")


def load_cache(name):
    f = ROOT / "data" / f"cache-{name}.json"
    return f, (json.loads(f.read_text(encoding="utf-8")) if f.exists() else {})


def save_cache(f, items):
    keep = {"link", "pub", "feed_title"}
    cache = {i["link"]: {k: v for k, v in i.items() if k not in keep} for i in items}
    f.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- news
def update_news(lang):
    cfg = FEEDS[lang]
    cache_file, cache = load_cache(lang)
    listing = http(f"{BASE}/{lang}/component/source/news/all",
                   data={"page": "1", "perPage": str(PER_PAGE)}, referer=f"{BASE}/{lang}/news/all")
    found = parse_list(listing)
    if not found:
        raise RuntimeError(f"{lang}: the list answered but held no items (page layout changed?)")
    now = utcnow().isoformat()
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
    items = [{"link": k, **v, "pub": v.get("published") or v["first_seen"], "feed_title": v["title"]}
             for k, v in cache.items()]
    items = sorted(items, key=lambda i: i["pub"], reverse=True)[:KEEP]
    save_cache(cache_file, items)
    write_feed(cfg, items, f"{BASE}/{lang}/news/all", "uk" if lang == "ua" else "en")
    print(f"news {lang}: {len(found)} listed, {reads} articles read, {len(items)} in feed")


# ---------------------------------------------------------------- research events
def parse_events(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for box in soup.select(".search-result"):
        a = box.select_one("a.title[href]") or box.select_one("a[href]")
        if not a:
            continue
        cat = a.select_one(".category")
        category = cat.get_text(" ", strip=True) if cat else ""
        if cat:
            cat.extract()
        title = a.get_text(" ", strip=True)
        mark = box.select_one(".mark")
        date_text = mark.get_text(" ", strip=True) if mark else ""
        when = parse_date(date_text)
        if title:
            out.append({"link": urllib.parse.urljoin(BASE, a["href"]), "title": title, "category": category,
                        "date_text": date_text, "event_at": when.isoformat() if when else None})
    return out


def event_html(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    body = soup.select_one("main .widget-content") or soup.select_one("main")
    if not body:
        return ""
    for bad in body.select("script, style, noscript, iframe, form"):
        bad.decompose()
    for tag in body.find_all(href=True):
        tag["href"] = urllib.parse.urljoin(BASE, tag["href"])
    for tag in body.find_all(src=True):
        tag["src"] = urllib.parse.urljoin(BASE, tag["src"])
    return str(body)


def update_events(name):
    cfg = EVENT_FEEDS[name]
    page_link = f"{BASE}/ua/research/events?category={cfg['category']}"
    cache_file, cache = load_cache(name)
    found = []
    for page in range(1, EVENT_PAGES + 1):
        url = page_link + (f"&page={page}" if page > 1 else "")
        found += parse_events(http(url))
    if not found:
        raise RuntimeError(f"{name}: the events page held no events (page layout changed?)")
    now = utcnow()
    for f in found:
        c = cache.setdefault(f["link"], {"first_seen": now.isoformat()})
        c.update({k: v for k, v in f.items() if v is not None})
    reads = 0
    for link, c in cache.items():
        if reads >= 8:
            break
        event_at = dt.datetime.fromisoformat(c["event_at"]) if c.get("event_at") else None
        last = dt.datetime.fromisoformat(c["last_read"]) if c.get("last_read") else None
        fresh = event_at is None or event_at > now - dt.timedelta(days=EVENT_REREAD_DAYS)
        due = last is None or (fresh and now - last > dt.timedelta(hours=6))
        if not due:
            continue
        reads += 1
        try:
            c["content"] = event_html(http(link))
            c["last_read"] = now.isoformat()
        except Exception as e:  # noqa: BLE001
            print(f"warning: {link}: {e}", file=sys.stderr)
    items = []
    for k, v in cache.items():
        # Dated by its announcement: the event's date for past events, when first seen for upcoming ones.
        pub = min(filter(None, [v.get("event_at"), v["first_seen"]]))
        title = f"{v['title']} ({v['date_text']})" if v.get("date_text") else v["title"]
        items.append({"link": k, **v, "pub": pub, "feed_title": title})
    items = sorted(items, key=lambda i: i["pub"], reverse=True)[:KEEP]
    save_cache(cache_file, items)
    write_feed(cfg, items, page_link, "uk")
    print(f"events {name}: {len(found)} listed, {reads} pages read, {len(items)} in feed")


def main():
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "data").mkdir(exist_ok=True)
    jobs = [(f"news {lang}", update_news, lang) for lang in FEEDS] + \
           [(f"events {name}", update_events, name) for name in EVENT_FEEDS]
    failed = []
    for label, fn, arg in jobs:
        try:
            fn(arg)
        except Exception as e:  # noqa: BLE001
            failed.append(label)
            print(f"error ({label}): {e}", file=sys.stderr)
    # Fail the run (GitHub emails you) if any feed could not be built; the others are still written.
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
