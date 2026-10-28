"""Read listing media from a rendered browser page, never by fetching a listing with requests.

An existing Chrome debugging endpoint is optional. A saved rendered HTML file works
without Chrome. Only explicitly identified floor-plan images are selected.
"""
from __future__ import annotations

import io
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

FLOOR = re.compile(r"floor[\s_-]*plans?", re.I)
BLOCKED = re.compile(r"access denied|verify (?:you are|you're) human|captcha|press [&a-z; ]*hold|pardon (?:our|the) interruption", re.I)
MAX_IMAGE = 20 * 1024 * 1024


class ListingHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images, self.scripts, self.title = [], [], ""
        self._script, self._title = None, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img":
            self.images.append({"src": a.get("src") or a.get("data-src"), "label": " ".join(str(v or "") for v in a.values())})
        if tag == "meta" and a.get("property") == "og:image":
            self.images.append({"src": a.get("content"), "label": "lead exterior candidate"})
        if tag == "script" and ("json" in a.get("type", "") or a.get("id") == "__NEXT_DATA__"):
            self._script = ""
        if tag == "title":
            self._title = True

    def handle_data(self, data):
        if self._script is not None:
            self._script += data
        if self._title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            try:
                self.scripts.append(json.loads(self._script))
            except ValueError:
                pass
            self._script = None
        if tag == "title":
            self._title = False


def floorplan_candidates(document: str, base_url: str) -> list[str]:
    parser = ListingHTML()
    parser.feed(document)
    candidates = [im["src"] for im in parser.images if FLOOR.search(im["label"]) and im["src"]]

    def walk(node, floor=False):
        if isinstance(node, dict):
            local_floor = floor or any(FLOOR.search(str(node.get(k, ""))) for k in ("type", "category", "caption", "name", "label")) or node.get("isFloorPlan") is True
            for k, v in node.items():
                media_key = k.lower() in {"url", "src", "contenturl", "originalurl", "imageurl", "uri"}
                if ((local_floor and media_key) or FLOOR.search(k)) and isinstance(v, str) and (v.startswith(("https://", "http://", "//")) or re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", v, re.I)):
                    candidates.append(v)
                # A listing's description can mention a floor plan; that must never
                # turn every photograph or property URL in the listing into a plan.
                walk(v, floor or bool(FLOOR.search(k)))
        elif isinstance(node, list):
            for child in node:
                walk(child, floor)
    for script in parser.scripts:
        walk(script)
    return list(dict.fromkeys(urljoin(base_url, u) for u in candidates if urlparse(urljoin(base_url, u)).scheme in ("http", "https")))


def browser_document(url: str, endpoint: str = "http://127.0.0.1:9222") -> str | None:
    """Use a fresh browser tab and close only that tab. Never work around a block."""
    import websocket  # optional browser support
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Listing must be an HTTP(S) URL")
    host = urlparse(endpoint)
    if host.scheme != "http" or host.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Chrome endpoint must be on localhost")
    target, ws = None, None
    try:
        r = requests.put(endpoint.rstrip("/") + "/json/new?" + url, timeout=5)
        r.raise_for_status()
        target = r.json()
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        number = 0
        def call(method, params):
            nonlocal number
            number += 1
            ws.send(json.dumps({"id": number, "method": method, "params": params}))
            while True:
                reply = json.loads(ws.recv())
                if reply.get("id") == number:
                    if "error" in reply:
                        raise RuntimeError("Browser evaluation failed")
                    return reply.get("result", {})
        deadline = time.monotonic() + 25
        document = ""
        while time.monotonic() < deadline:
            result = call("Runtime.evaluate", {"expression": "JSON.stringify({state:document.readyState,html:document.documentElement.outerHTML})", "returnByValue": True})
            value = json.loads(result.get("result", {}).get("value") or "{}")
            document = value.get("html", "")
            if BLOCKED.search(re.sub(r"<[^>]+>", " ", document)[:20000]):
                raise RuntimeError("Listing site blocked browser access")
            if value.get("state") == "complete" and len(document) > 1000:
                return document
            time.sleep(0.5)
        return document or None
    finally:
        if ws:
            ws.close()
        if target:
            try:
                requests.get(endpoint.rstrip("/") + "/json/close/" + target["id"], timeout=5)
            except requests.RequestException:
                pass


def crop_floorplan(image):
    """Trim separated branding/footer bands while retaining every substantial drawing band.

    If bands cannot be distinguished, retain the complete image for visual review.
    """
    gray = image.convert("L")
    width, height = gray.size
    rows = []
    pixels = gray.load()
    for y in range(height):
        if sum(pixels[x, y] < 220 for x in range(width)) >= max(3, width // 200):
            rows.append(y)
    bands = []
    for y in rows:
        if not bands or y - bands[-1][1] > max(12, height * 0.04):
            bands.append([y, y])
        else:
            bands[-1][1] = y
    def has_walls(band):
        tall = 0
        for x in range(width):
            run = longest = 0
            for y in range(band[0], band[1] + 1):
                run = run + 1 if pixels[x, y] < 220 else 0
                longest = max(longest, run)
            if longest >= (band[1] - band[0]) * 0.35:
                tall += 1
        return tall >= 3
    drawings = [b for b in bands if b[1] - b[0] >= max(80, height * 0.12) and has_walls(b)]
    if not drawings:
        return image
    top, bottom = drawings[0][0], drawings[-1][1] + 1
    box = gray.crop((0, top, width, bottom)).point(lambda p: 255 if p < 220 else 0).getbbox()
    if not box:
        return image
    pad = max(8, int(width * 0.01))
    return image.crop((max(0, box[0] - pad), max(0, top - pad), min(width, box[2] + pad), min(height, bottom + pad)))


def download_image(source: str, destination: Path, crop: bool = False) -> Path:
    """Bound downloads and decode actual image bytes before saving a normalized PNG."""
    from PIL import Image
    if urlparse(source).scheme in ("http", "https"):
        with requests.get(source, timeout=30, stream=True) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_IMAGE:
                    raise ValueError("Image exceeds 20 MB")
    else:
        if Path(source).stat().st_size > MAX_IMAGE:
            raise ValueError("Image exceeds 20 MB")
        data = Path(source).read_bytes()
    with Image.open(io.BytesIO(data)) as img:
        if img.width * img.height > 30_000_000:
            raise ValueError("Image exceeds 30 megapixels")
        img.load()
        if min(img.size) < 100:
            raise ValueError("Image is too small")
        result = img.convert("RGB")
        (crop_floorplan(result) if crop else result).save(destination, "PNG")
    return destination


def fetch_floorplan(listing: str | None, destination: Path, source: str | None = None,
                   html_path: str | None = None, endpoint: str = "http://127.0.0.1:9222") -> tuple[Path | None, str]:
    try:
        if source:
            return download_image(source, destination, crop=True), "explicit image"
        if not listing:
            return None, "no listing"
        document = Path(html_path).read_text() if html_path else browser_document(listing, endpoint)
        if not document:
            return None, "browser unavailable"
        for candidate in floorplan_candidates(document, listing):
            try:
                return download_image(candidate, destination, crop=True), "listing image"
            except (requests.RequestException, ValueError, OSError):
                continue
        return None, "no labeled floor plan found"
    except ImportError:
        return None, "browser/image packages unavailable"
    except Exception as exc:
        # URLs and browser errors may carry credentials; emit only a fixed category.
        return None, "listing blocked" if "blocked" in str(exc).lower() else "browser or image unavailable"
