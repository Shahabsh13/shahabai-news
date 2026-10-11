#!/usr/bin/env python3
"""Collect recent items from configured RSS feeds into data/news.json.

Uses only Python's standard library. This is a first-stage collector, not a
fact-checker: entries retain their original URL, source and publication time.
"""
from __future__ import annotations
import json, re, time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources.json"
OUTPUT = ROOT / "data" / "news.json"
WINDOW_HOURS = 48
MAX_PER_SOURCE = 20
USER_AGENT = "ShahabNewsCollector/1.0 (+https://shahab1380.github.io/shahabai-news/)"

def clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()

def parse_date(value: str):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()

def first_text(node, names):
    for child in node.iter():
        if local_name(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return ""

def parse_feed(xml_bytes, source, cutoff):
    root = ET.fromstring(xml_bytes)
    results = []
    for item in root.iter():
        tag = local_name(item.tag)
        if tag not in ("item", "entry"):
            continue
        title = clean(first_text(item, {"title"}))
        link = ""
        for child in item:
            if local_name(child.tag) == "link":
                link = (child.attrib.get("href") or child.text or "").strip()
                if child.attrib.get("rel") in (None, "alternate") and link:
                    break
        if not link:
            link = first_text(item, {"guid", "id"})
        published_raw = first_text(item, {"pubdate", "published", "updated", "date"})
        published = parse_date(published_raw)
        if not title or not link or not published or published < cutoff:
            continue
        description = clean(first_text(item, {"description", "summary", "encoded", "content"}))
        author = clean(first_text(item, {"creator", "author"}))
        results.append({"id": "", "title": title, "summary": description[:600], "category": source["category"], "source": source["name"], "source_id": source["id"], "url": link, "published_at": published.isoformat().replace("+00:00", "Z"), "language": source.get("language", "en"), "author": author or None, "image_url": None, "status": "needs_review"})
        if len(results) >= MAX_PER_SOURCE:
            break
    return results

def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    config = json.loads(SOURCES.read_text(encoding="utf-8"))
    collected, errors = [], []
    for source in config["sources"]:
        try:
            req = Request(source["url"], headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
            with urlopen(req, timeout=25) as response:
                data = response.read(4_000_000)
            collected.extend(parse_feed(data, source, cutoff))
            print(f"OK {source['id']}: collected feed")
        except (HTTPError, URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            errors.append({"source_id": source.get("id", "unknown"), "error": str(exc)[:240]})
            print(f"WARN {source.get('id', 'unknown')}: {exc}")
        time.sleep(0.4)
    seen, unique = set(), []
    for item in sorted(collected, key=lambda x: x["published_at"], reverse=True):
        key = re.sub(r"[^a-z0-9\u0600-\u06ff]", "", item["title"].lower())
        url_key = item["url"].split("?")[0].rstrip("/").lower()
        dedupe_key = url_key or key
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        item["id"] = f"{item['source_id']}-{len(unique)+1}"
        unique.append(item)
    payload = {"generated_at": now.isoformat().replace("+00:00", "Z"), "window_hours": WINDOW_HOURS, "total": len(unique), "source_count": len(config["sources"]), "errors": errors, "items": unique, "note": "Collected automatically from RSS feeds. Items require editorial verification before being treated as confirmed news."}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(unique)} unique items to {OUTPUT}; {len(errors)} feed errors")

if __name__ == "__main__":
    main()
