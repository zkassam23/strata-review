"""Heuristic handlers behind MockLLM. Keyword and regex rules, nothing more.

They parse the same user message the real model receives (see prompts.py) and return a
payload that satisfies the task's JSON schema. Extraction handlers are added per stage.
"""
from __future__ import annotations

import re
from datetime import date

from . import prompts

_PAGE_RE = re.compile(r"^=== PAGE (?P<doc>[^:]+):(?P<page>\d+) ===$", re.M)

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], start=1)}
_DATE_RES = [
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b"),          # 14 April 2026
    re.compile(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b"),        # April 14, 2026
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),                    # 2026-04-14
]


def split_pages(user: str) -> list[tuple[str, int, str]]:
    out = []
    matches = list(_PAGE_RE.finditer(user))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(user)
        out.append((m.group("doc"), int(m.group("page")), user[m.end():end].strip()))
    return out


def find_date(text: str) -> str | None:
    """First parseable date in the text, ISO formatted. Tolerates common OCR noise."""
    t = text.replace("|", "l")
    for rx in _DATE_RES:
        for m in rx.finditer(t):
            g = m.groups()
            try:
                if rx is _DATE_RES[2]:
                    d = date(int(g[0]), int(g[1]), int(g[2]))
                elif rx is _DATE_RES[0]:
                    mon = MONTHS.get(g[1].lower())
                    if not mon:
                        continue
                    d = date(int(g[2]), mon, int(g[0]))
                else:
                    mon = MONTHS.get(g[0].lower())
                    if not mon:
                        continue
                    d = date(int(g[2]), mon, int(g[1]))
                if 1990 <= d.year <= 2040:
                    return d.isoformat()
            except ValueError:
                continue
    return None


# ---- classification -------------------------------------------------------------------

_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "council_minutes": [("council meeting", 3), ("minutes of the council", 3), ("strata council", 2),
                        ("council members present", 2), ("call to order", 1), ("adjourn", 1), ("motion", 0.5)],
    "agm_sgm_minutes": [("annual general meeting", 4), ("special general meeting", 4), ("agm", 2), ("sgm", 2),
                        ("proxies", 1.5), ("quorum", 1), ("owners present", 1)],
    "depreciation_report": [("depreciation report", 4), ("reserve fund study", 3), ("funding model", 2),
                            ("useful life", 2), ("component inventory", 2), ("remaining life", 1.5)],
    "form_b": [("form b", 5), ("information certificate", 4), ("strata property act", 1), ("strata lot", 1),
               ("section 59", 3)],
    "financial_statements": [("balance sheet", 3), ("statement of financial position", 3), ("contingency reserve fund", 1.5),
                             ("operating fund", 2), ("income statement", 2), ("statement of operations", 3),
                             ("accounts receivable", 1.5), ("budget", 1), ("fiscal year", 1)],
    "insurance_summary": [("insurance summary", 4), ("certificate of insurance", 4), ("deductible", 2.5),
                          ("policy period", 2), ("insured", 1.5), ("insurer", 2)],
    "engineering_report": [("building envelope", 3), ("envelope assessment", 4), ("condition assessment", 3),
                           ("sealant", 2), ("remediation", 2), ("engineer", 1.5), ("elevation", 1)],
    "bylaws": [("bylaws", 3), ("bylaw", 2), ("division", 1), ("rental restriction", 1), ("standard bylaws", 3),
               ("rules of the strata", 1)],
}


def _score_page(text: str) -> tuple[str, float, str | None]:
    low = text.lower()
    header = "\n".join(low.splitlines()[:2])
    scores: dict[str, float] = {}
    for dt, kws in _KEYWORDS.items():
        s = 0.0
        for kw, w in kws:
            s += w * min(3, low.count(kw))
            if kw in header:
                s += 4 * w   # a running header names the document; weight it like a model would
        scores[dt] = s
    # running headers in minutes are the strongest cue
    if re.search(r"council meeting minutes", low) or re.search(r"minutes of (the|a) (strata )?council", low):
        scores["council_minutes"] += 4
    if re.search(r"(annual|special) general meeting", low):
        scores["agm_sgm_minutes"] += 4
    best = max(scores, key=scores.get)
    top = scores[best]
    others = sorted(scores.values(), reverse=True)
    second = others[1] if len(others) > 1 else 0.0
    if top <= 0:
        return "other", 0.2, None
    margin = (top - second) / top
    conf = round(min(0.98, 0.45 + 0.5 * margin + min(0.1, top / 40)), 2)
    if len(text) < 200:
        conf = round(conf * 0.8, 2)
    title = None
    for line in text.splitlines()[:6]:
        if line.strip() and len(line.strip()) < 90 and line.strip().upper() == line.strip():
            title = line.strip()
            break
    return best, conf, title


def classify_pages(user: str) -> dict:
    pages = []
    for _doc, pno, text in split_pages(user):
        dt, conf, title = _score_page(text)
        mdate = None
        if dt in ("council_minutes", "agm_sgm_minutes"):
            head = "\n".join(text.splitlines()[:8])
            mdate = find_date(head) or find_date(text)
        pages.append({"page_number": pno, "doc_type": dt, "confidence": conf, "meeting_date": mdate, "title": title})
    return {"pages": pages}


HANDLERS = {
    "classify_pages": classify_pages,
}
