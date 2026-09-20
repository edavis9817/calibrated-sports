"""RSS and Atom, reduced to four fields: headline, link, source, timestamp.

THE BODY IS DROPPED AT THE PARSER, not filtered later. `<description>`, `<summary>`,
`<content:encoded>` and their Atom equivalents are never read into a return value, the
store has no column that could hold them (`feeds.schema.NEWS_FORBIDDEN`), and a test
asserts both. The only words this project publishes about someone else's reporting are
the headline they wrote and a link back to them.

Stdlib XML, no feed library: the parse is fifty lines and a dependency that helpfully
carries `entry.summary` is a dependency that will one day be read.
"""
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

ATOM = "{http://www.w3.org/2005/Atom}"
KEPT = ("title", "link", "source", "published_ts", "guid")


def _text(el):
    return (el.text or "").strip() if el is not None else None


def _ts(value):
    """RFC 822 (RSS) or ISO 8601 (Atom). Unparseable is None, never 'now': a made-up
    timestamp would make a story look newer than it is."""
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        v = value.replace("Z", "+00:00")
        d = datetime.fromisoformat(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def _atom_link(entry):
    for link in entry.findall(f"{ATOM}link"):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href")
    return None


def parse(body: bytes, feed: str, source_fallback=None):
    """[{title, link, source, published_ts, guid}] - and nothing else, ever."""
    root = ElementTree.fromstring(body)
    channel = root.find("channel")
    out = []
    if channel is not None:                                   # RSS 2.0
        source = _text(channel.find("title")) or source_fallback or feed
        for item in channel.findall("item"):
            link = _text(item.find("link"))
            title = _text(item.find("title"))
            guid = _text(item.find("guid")) or link or title
            if not (title and guid):
                continue
            out.append({"title": title, "link": link, "source": source,
                        "published_ts": _ts(_text(item.find("pubDate"))), "guid": guid})
        return out
    source = _text(root.find(f"{ATOM}title")) or source_fallback or feed
    for entry in root.findall(f"{ATOM}entry"):               # Atom
        link = _atom_link(entry)
        title = _text(entry.find(f"{ATOM}title"))
        guid = _text(entry.find(f"{ATOM}id")) or link or title
        if not (title and guid):
            continue
        published = _text(entry.find(f"{ATOM}published")) or _text(entry.find(f"{ATOM}updated"))
        out.append({"title": title, "link": link, "source": source,
                    "published_ts": _ts(published), "guid": guid})
    return out


def rows(items, sport, feed):
    """Schema order for `news_items`."""
    return [(sport, feed, i["guid"], i["title"], i["link"], i["source"], i["published_ts"])
            for i in items]
