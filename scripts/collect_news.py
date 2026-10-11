#!/usr/bin/env python3
"""Collect recent RSS/Atom news; preserve source links and available feed images."""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCES_FILE = ROOT / "sources.json"
OUTPUT_FILE = ROOT / "data" / "news.json"
WINDOW_HOURS = 48
MAX_PER_SOURCE = 20
USER_AGENT = "ShahabNewsCollector/1.1"

def clean(value):
    value = unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()

def tag_name(node):
    return node.tag.rsplit("}", 1)[-1].lower()

def first_text(node, names):
    for item in node.iter():
        if tag_name(item) in names and item.text and item.text.strip():
            return clean(item.text)
    return ""

def find_image(node):
    for item in node.iter():
        tag = tag_name(item)
        attrs = {k.rsplit("}", 1)[-1].lower(): v for k, v in item.attrib.items()}
        candidate = ""
        if tag in {"thumbnail", "content", "enclosure"}:
            mime = attrs.get("type", "").lower()
            candidate = attrs.get("url", "")
            if mime and not mime.startswith("image/"):
                candidate = ""
        elif tag == "link" and attrs.get("rel", "").lower() == "enclosure" and attrs.get("type", "").lower().startswith("image/"):
            candidate = attrs.get("href", "")
        elif tag in {"image_url", "thumbnail_url"} and item.text:
            candidate = item.text.strip()
        if candidate.startswith(("https://", "http://")):
            return candidate
    return None

def parse_date(node):
    for item in node.iter():
        if tag_name(item) not in {"pubdate", "published", "updated", "date", "issued"} or not item.text:
            continue
        raw = item.text.strip()
        try:
            dt = parsedate_to_datetime(raw)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None

def canonical_url(url):
    try:
        p = urlsplit(url.strip())
        if p.scheme not in ("http", "https") or not p.netloc:
            return url.strip()
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "ocid"}]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(query), ""))
    except Exception:
        return url.strip()

def category_for(title, summary, fallback="other"):
    value = (title + " " + summary).lower()
    rules = [
        ("gold_currency", ["قیمت طلا", "قیمت دلار", "نرخ ارز", "سکه", "gold price", "currency rates"]),
        ("accounting_tax", ["مالیات", "حسابداری", "تامین اجتماعی", "تأمین اجتماعی", "بیمه", "حقوق و دستمزد", "سامانه مودیان", "ارزش افزوده", "اظهارنامه"]),
        ("android_games", ["android game", "android games", "google play", "play store", "بازی اندروید", "بازی موبایل"]),
        ("ai", ["artificial intelligence", "generative ai", "openai", "chatgpt", "هوش مصنوعی", "مدل زبانی"]),
        ("fpl", ["fantasy premier league", " fpl ", "فانتزی لیگ برتر"]),
        ("iran_football", ["پرسپولیس", "استقلال", "تراکتور", "سپاهان", "تیم ملی ایران", "لیگ برتر ایران", "فوتبال ایران"]),
        ("world_football", ["premier league", "champions league", "la liga", "bundesliga", "serie a", "لیگ قهرمانان", "لیگ برتر انگلیس", "فوتبال اروپا", "فوتبال جهان"]),
        ("gaming", ["playstation", "xbox", "nintendo", "steam", "gaming industry", "بازی ویدیویی", "صنعت بازی", "گیمینگ"]),
        ("other_sports", ["tennis", "basketball", "formula 1", "olympics", "تنیس", "بسکتبال", "فرمول یک"]),
    ]
    padded = " " + value + " "
    for category, keywords in rules:
        if any(k in padded for k in keywords):
            return category
    return fallback or "other"

def main():
    sources = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    if isinstance(sources, dict):
        sources = sources.get("sources", [])
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    collected, errors = [], []

    for source in sources:
        feed_url = source.get("url", "")
        source_name = source.get("name") or source.get("title") or feed_url
        fallback = source.get("category", "other")
        try:
            request = Request(feed_url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
            with urlopen(request, timeout=20) as response:
                root = ET.fromstring(response.read())
            entries = [n for n in root.iter() if tag_name(n) in {"item", "entry"}]
            added = 0
            for entry in entries:
                title = first_text(entry, {"title"})
                link = ""
                for node in entry.iter():
                    if tag_name(node) == "link":
                        attrs = {k.rsplit("}", 1)[-1].lower(): v for k, v in node.attrib.items()}
                        candidate = attrs.get("href") or (node.text or "").strip()
                        if candidate and attrs.get("rel", "alternate") in {"alternate", ""}:
                            link = candidate
                            break
                if not link:
                    link = first_text(entry, {"guid", "id"})
                link = canonical_url(link)
                if not title or not link.startswith(("http://", "https://")):
                    continue
                published = parse_date(entry)
                if published and (published < cutoff or published > now + timedelta(hours=2)):
                    continue
                summary = first_text(entry, {"description", "summary", "encoded", "content"})
                collected.append({
                    "id": hashlib.sha256(link.encode("utf-8")).hexdigest()[:16],
                    "title": title, "url": link, "source": source_name,
                    "published_at": published.isoformat().replace("+00:00", "Z") if published else None,
                    "summary": summary[:1200], "category": category_for(title, summary, fallback),
                    "image_url": find_image(entry), "status": "needs_review"
                })
                added += 1
                if added >= MAX_PER_SOURCE:
                    break
        except Exception as exc:
            errors.append({"source": source_name, "error": str(exc)[:300]})

    unique, seen_urls, seen_titles = [], set(), set()
    for item in sorted(collected, key=lambda x: x.get("published_at") or "", reverse=True):
        title_key = re.sub(r"[^\w\u0600-\u06FF]+", "", item["title"].lower())
        if item["url"] in seen_urls or (title_key and title_key in seen_titles):
            continue
        seen_urls.add(item["url"])
        if title_key:
            seen_titles.add(title_key)
        unique.append(item)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps({
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "window_hours": WINDOW_HOURS, "count": len(unique),
        "feed_errors": errors, "items": unique
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(unique)} unique items; feed errors: {len(errors)}")

if __name__ == "__main__":
    main()
