#!/usr/bin/env python
"""Notion helpers shared by the underwriting scripts: rate-limited requests, markdown to blocks, state file.

Env: NOTION_TOKEN or NOTION_API_TOKEN (never printed, never written to a page). State (database and page ids) lives in
data/notion_state.json. Copied from the nyc-zr zoning mirror's helpers on 2026-10-17 when the underwriting system moved here.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "notion_state.json"
TOKEN = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_TOKEN")
if not TOKEN:
    sys.exit("set NOTION_TOKEN or NOTION_API_TOKEN")
API = "https://api.notion.com/v1"
MIN_INTERVAL = 0.36        # seconds between calls (Notion allows ~3 per second)
RICH_LIMIT = 2000          # chars per rich_text item
TABLE_ROWS_PER_BLOCK = 99  # header + 99 rows keeps a table under the 100-children limit
_last = 0.0


def req(method: str, path: str, body=None, version="2025-09-03"):
    """One Notion API call with pacing, retries on network errors and 429s; raises RuntimeError on 4xx/5xx."""
    global _last
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        try:
            r = requests.request(method, API + path, json=body, timeout=60,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Notion-Version": version,
                                          "Content-Type": "application/json"})
        except requests.RequestException as e:
            if attempt == 5:
                raise
            print(f"  retry ({e.__class__.__name__})", file=sys.stderr)
            time.sleep(2)
            continue
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "2")))
            continue
        if r.status_code in (500, 502, 503, 504) and method.upper() in ("GET", "PATCH") and attempt < 5:
            print(f"  retry ({r.status_code})", file=sys.stderr)
            time.sleep(min(2 ** attempt, 16))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()
    raise RuntimeError("gave up")


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def page_url(page_id: str) -> str:
    return "https://www.notion.so/" + page_id.replace("-", "")


def location_value(name, lat, lon, address=None):
    """A native Place value, or None when coordinates are unknown or invalid."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    place = {"lat": lat, "lon": lon, "name": name}
    if address:
        place["address"] = address
    return {"place": place}


# ----------------------------------------------------------------------------- markdown -> blocks
INLINE_RE = re.compile(
    r"\*\*_(.+?)_\*\*"                      # bold italic
    r"|\*\*(.+?)\*\*"                       # bold
    r"|(?<!\\)\*(.+?)(?<!\\)\*"             # italic *x*
    r"|\[([^\]]+)\]\(([^)\s]+)\)"           # link
    r"|(?<![\w*\\])_([^_\n]+?)_(?![\w*])"   # italic _x_
)


def rich_text(md: str) -> list[dict]:
    """Inline markdown -> Notion rich_text array (bold/italic/links, nesting allowed), chunked to 2000 chars."""
    items: list[dict] = []

    def push(text: str, bold: bool, italic: bool, link: str | None):
        text = text.replace("\\*", "*")
        for i in range(0, len(text), RICH_LIMIT):
            chunk = text[i:i + RICH_LIMIT]
            if not chunk:
                continue
            it = {"type": "text", "text": {"content": chunk}}
            if link:
                it["text"]["link"] = {"url": link}
            ann = {}
            if bold:
                ann["bold"] = True
            if italic:
                ann["italic"] = True
            if ann:
                it["annotations"] = ann
            items.append(it)

    def parse(s: str, bold=False, italic=False, link=None, depth=0):
        pos = 0
        for m in (INLINE_RE.finditer(s) if depth < 4 else []):
            if m.start() > pos:
                push(s[pos:m.start()], bold, italic, link)
            if m.group(1) is not None:
                parse(m.group(1), True, True, link, depth + 1)
            elif m.group(2) is not None:
                parse(m.group(2), True, italic, link, depth + 1)
            elif m.group(3) is not None:
                parse(m.group(3), bold, True, link, depth + 1)
            elif m.group(4) is not None:
                parse(m.group(4), bold, italic, m.group(5), depth + 1)
            else:
                parse(m.group(6), bold, True, link, depth + 1)
            pos = m.end()
        if pos < len(s):
            push(s[pos:], bold, italic, link)

    parse(md)
    return items[:100] or [{"type": "text", "text": {"content": ""}}]


def rt(s: str) -> list[dict]:
    """rich_text() plus `inline code` spans."""
    out: list[dict] = []
    for i, part in enumerate(re.split(r"(`[^`\n]+`)", s)):
        if not part:
            continue
        if i % 2 == 1:
            out.append({"type": "text", "text": {"content": part[1:-1]}, "annotations": {"code": True}})
        else:
            out.extend(x for x in rich_text(part) if x["text"]["content"])
    return out[:100] or [{"type": "text", "text": {"content": ""}}]


def table_blocks(md: str) -> list[dict]:
    lines = [ln for ln in md.splitlines() if ln.strip()]
    rows = []
    for ln in lines:
        if re.match(r"^\|[\s\-|]+\|$", ln):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split(" | ")]
        rows.append([c.replace("\\|", "|") for c in cells])
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    blocks = []
    for i in range(0, max(1, len(body)), TABLE_ROWS_PER_BLOCK):
        chunk = body[i:i + TABLE_ROWS_PER_BLOCK]
        children = [{"type": "table_row", "table_row": {"cells": [rich_text(c) for c in header]}}]
        children += [{"type": "table_row", "table_row": {"cells": [rich_text(c) for c in r]}} for r in chunk]
        blocks.append({"type": "table", "table": {"table_width": width, "has_column_header": True,
                                                   "has_row_header": False, "children": children}})
    return blocks


def md_to_blocks(md: str) -> list[dict]:
    """Block-level markdown (headings 1-3, lists, tables, fenced code, quotes, dividers, paragraphs) -> Notion blocks."""
    blocks: list[dict] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush():
        if para:
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": rt(" ".join(x.strip() for x in para))}})
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            lang = ln[3:].strip() or "plain text"
            lang = {"json": "json", "bash": "bash", "python": "python", "": "plain text"}.get(lang, "plain text")
            j = i + 1
            code: list[str] = []
            while j < len(lines) and not lines[j].startswith("```"):
                code.append(lines[j])
                j += 1
            text = "\n".join(code)
            blocks.append({"type": "code", "code": {"language": lang, "rich_text": [
                {"type": "text", "text": {"content": text[k:k + 2000]}} for k in range(0, max(1, len(text)), 2000)]}})
            i = j + 1
            continue
        if ln.startswith("|"):
            flush()
            j = i
            tbl: list[str] = []
            while j < len(lines) and lines[j].startswith("|"):
                tbl.append(lines[j])
                j += 1
            blocks.extend(table_blocks("\n".join(tbl)))
            i = j
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", ln)
        if m:
            flush()
            lvl = len(m.group(1))
            blocks.append({"type": f"heading_{lvl}", f"heading_{lvl}": {"rich_text": rt(m.group(2))}})
            i += 1
            continue
        m = re.match(r"^\s*[-*]\s+(.*)$", ln)
        if m:
            flush()
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(m.group(1))}})
            i += 1
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)$", ln)
        if m:
            flush()
            blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": rt(m.group(1))}})
            i += 1
            continue
        if ln.startswith("> "):
            flush()
            blocks.append({"type": "quote", "quote": {"rich_text": rt(ln[2:])}})
            i += 1
            continue
        if ln.strip() == "---":
            flush()
            blocks.append({"type": "divider", "divider": {}})
            i += 1
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        para.append(ln)
        i += 1
    flush()
    return blocks


# ----------------------------------------------------------------------------- block ops
def list_children(block_id: str) -> list[dict]:
    out, cursor = [], None
    while True:
        q = f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        r = req("GET", q)
        out.extend(r["results"])
        if not r.get("has_more"):
            return out
        cursor = r["next_cursor"]


def append(block_id: str, blocks: list[dict]):
    for k in range(0, len(blocks), 100):
        req("PATCH", f"/blocks/{block_id}/children", {"children": blocks[k:k + 100]})
