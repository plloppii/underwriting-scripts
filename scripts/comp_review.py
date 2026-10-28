"""Sale-specific listing evidence, kept separately so fresh API runs retain the review."""
from __future__ import annotations

import copy
import json
import datetime as dt
import re
from pathlib import Path
from urllib.parse import urlparse

CONDITIONS = {"unknown", "as_is", "renovated", "new", "exclude"}


def read_review(path: str | None) -> dict:
    review = json.loads(Path(path).read_text()) if path else {"sales": []}
    if not isinstance(review, dict) or not isinstance(review.get("sales"), list):
        raise ValueError("Review must contain a sales array")
    if "include_unreviewed" in review and not isinstance(review["include_unreviewed"], bool):
        raise ValueError("include_unreviewed must be true or false")
    seen = set()
    for sale in review["sales"]:
        key = (sale.get("bbl"), sale.get("date"))
        if not all(isinstance(x, str) and x for x in key) or key in seen:
            raise ValueError("Each review needs a unique BBL and sale date")
        seen.add(key)
        if not re.fullmatch(r"[1-5]\d{9}", key[0]):
            raise ValueError(f"Invalid BBL: {key[0]}")
        dt.date.fromisoformat(key[1])
        if sale.get("condition", "unknown") not in CONDITIONS:
            raise ValueError(f"Invalid comp condition for {key}")
        source = urlparse(sale.get("url") or "")
        if sale.get("condition") in {"as_is", "renovated", "new", "exclude"} and not (source.scheme == "https" and source.hostname and sale.get("evidence")):
            raise ValueError(f"Condition decisions need an HTTPS source and evidence: {key}")
        for name in ("list_price", "days_on_market"):
            value = sale.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"Invalid {name} for {key}")
    return review


def review_template(comps: list[dict]) -> dict:
    return {"sales": [{"bbl": c["bbl"], "date": c["date"], "address": c["address"],
                       "condition": "unknown", "url": None, "evidence": None,
                       "list_price": None, "days_on_market": None, "beds_baths": None} for c in comps]}


def apply_review(comps: list[dict], review: dict) -> tuple[list[dict], list[dict], list[dict]]:
    indexed = {(s["bbl"], s["date"]): s for s in review["sales"]}
    purchase, exits, excluded = [], [], []
    for c in copy.deepcopy(comps):
        evidence = indexed.get((c["bbl"], c["date"]))
        if evidence is None and review.get("include_unreviewed") is False:
            c["review_status"] = "unreviewed"
            excluded.append(c)
            continue
        if evidence:
            c["review"] = evidence
            if evidence.get("condition") == "as_is":
                c["reno"] = None  # actual listing evidence supersedes a permit signal
        condition = (evidence or {}).get("condition", "unknown")
        (exits if condition in {"renovated", "new"} else excluded if condition == "exclude" else purchase).append(c)
    return purchase, exits, excluded


def listing_cell(comp: dict) -> str:
    review = comp.get("review") or {}
    parts = []
    if review.get("list_price") is not None:
        parts.append(f"${review['list_price']:,.0f} list")
    if review.get("days_on_market") is not None:
        parts.append(f"{review['days_on_market']:g} days")
    if review.get("url"):
        parts.append(f"[listing]({review['url']})")
    if review.get("not_listed"):
        parts.append("not listed")
    return " · ".join(parts) or comp.get("reno") or ""
