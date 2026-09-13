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


# ---- extraction -------------------------------------------------------------------------
# Regex/keyword extraction that emits the same fact shape as the real model. Quotes are cut
# straight from the page text so they anchor; the anchor rule in extract.py still runs on them.

_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
_HEAD = re.compile(r"^(?P<hd>SECTION|SOURCE FILE|PAGES IN THIS CALL|MEETING DATE|UNIT UNDER REVIEW): (?P<v>.+)$", re.M)
_ITEM = re.compile(r"^(\d{1,2})\.\s+(?P<heading>[A-Z][^\n]{2,80})$", re.M)


def rx(pattern: str, flags=re.I):
    """Compile a pattern with literal spaces made whitespace-tolerant (PDF text wraps lines)."""
    return re.compile(pattern.replace(" ", r"\s+"), flags)


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.;])\s+", re.sub(r"\s+", " ", text)) if x.strip()]


_BIG_WORK = ("roof", "membrane", "parkade", "elevator", "envelope", "piping", "boiler", "window", "cladding", "balcon", "plumbing", "sealant")
_LITIGATION = re.compile(r"\b(lawsuit|court|tribunal|civil resolution|legal action|claim against the strata|litigation|arbitration)\b", re.I)


def money(text: str) -> list[float]:
    out = []
    for m in _MONEY.finditer(text):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def _header(user: str) -> dict[str, str]:
    return {m.group("hd"): m.group("v").strip() for m in _HEAD.finditer(user.split("=== PAGE", 1)[0])}


def _quote(text: str, needle: str | None = None, n: int = 180) -> str:
    """A verbatim slice of the page text: the sentence containing needle, else the start."""
    t = text.strip()
    if needle:
        i = t.lower().find(needle.lower())
        if i >= 0:
            start = max(t.rfind(". ", 0, i) + 2, t.rfind("\n", 0, i) + 1, 0)
            return t[start:start + n].strip()
    return t[:n].strip()


def _fact(cat, kind, summary, quote, page, *, topic=None, status=None, date=None, amin=None, amax=None, data=None):
    return {"category": cat, "kind": kind, "summary": summary, "quote": quote, "page_numbers": [page],
            "topic": topic, "status": status, "date": date, "amount_min": amin, "amount_max": amax,
            "data": [{"key": k, "value": str(v)} for k, v in (data or {}).items()]}


def _items(text: str) -> list[tuple[str, str]]:
    """Split minutes page text into (heading, body) agenda items."""
    ms = list(_ITEM.finditer(text))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out.append((m.group("heading").strip(), text[m.end():end].strip()))
    return out


def _minutes_item(heading: str, body: str, page: int, mdate: str | None) -> list[dict]:
    low = re.sub(r"\s+", " ", (heading + " " + body)).lower()
    hlow = heading.lower()
    sents = _sentences(body)
    facts = []
    roofish = any(w in low for w in ("roof", "membrane"))
    workish = any(w in hlow for w in _BIG_WORK) or any(w in low for w in ("replacement", "remediation"))
    quote_sents = [x for x in sents if "quote" in x.lower()]
    quote_amts = [a for x in quote_sents for a in money(x) if a >= 50000]
    big = quote_amts or [a for a in money(body) if a >= 100000]
    is_quote_item = bool(big) and workish
    if "levy" in low and "carried" in low and "levy resolution" in low and "defer" not in low:
        facts.append(_fact("special_levies", "levy_passed", f"Special levy resolution passed: {heading}", _quote(body, "levy"), page,
                           topic=hlow, status="approved", date=mdate, amin=min(big) if big else None, amax=max(big) if big else None))
    elif is_quote_item or (roofish and ("assessment" in low or "condition" in low) and "leak" not in hlow):
        topic = "roof membrane replacement" if roofish else hlow
        received = any(re.search(r"quotes?\b[^.]{0,60}\breceived", x, re.I) for x in sents)
        if "defer" in low:
            status, kind = "deferred", "work_deferred"
        elif received and big:
            status, kind = "quotes_received", "quote_obtained"
        elif "obtain" in low and "quote" in low:
            status, kind = "quotes_requested", "major_work_discussed"
        elif "levy" in low and ("resolution" in low or "propose" in low):
            status, kind = "proposed", "levy_proposed"
        else:
            status, kind = "discussed", "major_work_discussed"
        facts.append(_fact("special_levies", kind, f"{heading}: {body[:140]}", _quote(body, "quote" if "quote" in low else None), page,
                           topic=topic, status=status, date=mdate, amin=min(big) if big else None, amax=max(big) if big else None,
                           data={"heading": heading}))
    if any(w in low for w in ("leak", "water staining", "water ingress", "ingress")) and "insurance" not in low:
        small = [a for a in money(body) if a < 50000]
        facts.append(_fact("building_envelope", "water_ingress", f"{heading}: {body[:140]}", _quote(body, "leak"), page,
                           topic="roof membrane leaks" if roofish else "water ingress", status="discussed", date=mdate,
                           amin=min(small) if small else None, amax=max(small) if small else None))
    if "sealant" in low or "envelope" in low:
        st = "completed" if any(w in low for w in ("completed", "complete", "finished")) else ("tendered" if "tender" in low else "discussed")
        facts.append(_fact("building_envelope", "envelope_work_completed" if st == "completed" else ("envelope_work_tendered" if st == "tendered" else "envelope_finding"),
                           f"{heading}: {body[:140]}", _quote(body, "sealant" if "sealant" in low else "envelope"), page,
                           topic="building envelope sealant remediation", status=st, date=mdate))
    m = rx(r"contingency reserve fund balance was \$([\d,]+)").search(body)
    if m:
        v = float(m.group(1).replace(",", ""))
        facts.append(_fact("contingency_reserve", "crf_balance", f"Treasurer reported contingency reserve fund balance of ${m.group(1)}",
                           _quote(body, "contingency reserve fund balance"), page, topic="contingency reserve balance", date=mdate, amin=v, amax=v))
    m = rx(r"contribution to the contingency reserve fund of \$([\d,]+)").search(body)
    if m:
        v = float(m.group(1).replace(",", ""))
        facts.append(_fact("contingency_reserve", "crf_annual_contribution", f"Annual contribution to the contingency reserve fund of ${m.group(1)}",
                           _quote(body, "contribution to the contingency"), page, topic="contingency reserve contribution", date=mdate, amin=v, amax=v))
    if "deductible" in low:
        m = rx(r"deductible to \$([\d,]+)").search(body)
        v = float(m.group(1).replace(",", "")) if m else None
        peril = "water damage" if "water" in low else "unspecified"
        facts.append(_fact("insurance_deductibles", "deductible_change" if m else "deductible", f"{heading}: {body[:140]}", _quote(body, "deductible"), page,
                           topic=f"{peril} deductible", status="discussed", date=mdate, amin=v, amax=v, data={"peril": peril}))
    if "rental" in low and "bylaw" in low:
        facts.append(_fact("bylaws", "rental_restriction", f"{heading}: {body[:140]}", _quote(body, "rental"), page,
                           topic="rental restriction bylaw 41", status="discussed", date=mdate, data={"section": "41"}))
    if _LITIGATION.search(low):
        facts.append(_fact("litigation", "litigation_threatened", f"{heading}: {body[:140]}", _quote(body, _LITIGATION.search(low).group(0)), page,
                           topic=hlow, status="discussed", date=mdate))
    m = rx(r"depreciation report[^.]*?(?:dated|updated in|from) ([A-Za-z]+ \d{4})").search(body)
    if m:
        rd = find_date("1 " + m.group(1))
        facts.append(_fact("depreciation_report_currency", "depreciation_report_date", f"Depreciation report referred to as dated {m.group(1)}",
                           _quote(body, "depreciation report"), page, topic="depreciation report", status="discussed", date=mdate,
                           data={"report_date": rd or m.group(1)}))
    return facts


def extract_minutes(user: str) -> dict:
    h = _header(user)
    mdate = h.get("MEETING DATE") or None
    facts = []
    for _doc, pno, text in split_pages(user):
        if not mdate:
            mdate = find_date(text)
        for heading, body in _items(text):
            facts += _minutes_item(heading, body, pno, mdate)
        # AGM-style prose with a levy expectation and a cost range but no numbered quote item
        low = text.lower()
        if "special levy" in low and "roof" in low and not any(f["category"] == "special_levies" for f in facts if f["page_numbers"] == [pno]):
            big = [a for x in _sentences(text) if ("roof" in x.lower() or "levy" in x.lower()) for a in money(x) if a >= 50000]
            facts.append(_fact("special_levies", "major_work_discussed", "Roof replacement and a likely special levy reported to owners",
                               _quote(text, "special levy"), pno, topic="roof membrane replacement", status="discussed", date=mdate,
                               amin=min(big) if big else None, amax=max(big) if big else None))
    return {"facts": facts}


def extract_depreciation(user: str) -> dict:
    facts = []
    rdate = None
    for _doc, pno, text in split_pages(user):
        low = text.lower()
        m = rx(r"report date:\s*([0-9]{1,2} [A-Za-z]+ [0-9]{4})").search(text)
        if m and not rdate:
            rdate = find_date(m.group(1))
            facts.append(_fact("depreciation_report_currency", "depreciation_report_date", f"Depreciation report dated {m.group(1)}",
                               _quote(text, "report date"), pno, topic="depreciation report", date=rdate, data={"report_date": rdate}))
        m = rx(r"recommended contingency reserve fund balance:?\s*\$([\d,]+) by (?:fiscal )?(\d{4})").search(text) or \
            rx(r"target balance of \$([\d,]+) by (?:fiscal )?(\d{4})").search(text)
        if m:
            v = float(m.group(1).replace(",", ""))
            facts.append(_fact("contingency_reserve", "crf_recommended_balance", f"Recommended contingency reserve balance ${m.group(1)} by {m.group(2)}",
                               _quote(text, m.group(0)[:30]), pno, topic="contingency reserve balance", status="recommended", date=rdate,
                               amin=v, amax=v, data={"target_year": m.group(2)}))
        m = rx(r"fund balance at (\d{1,2} [A-Za-z]+ \d{4}) was \$([\d,]+)").search(text)
        if m:
            v = float(m.group(2).replace(",", ""))
            facts.append(_fact("contingency_reserve", "crf_balance", f"Contingency reserve balance ${m.group(2)} at {m.group(1)}",
                               _quote(text, "fund balance at"), pno, topic="contingency reserve balance", date=find_date(m.group(1)), amin=v, amax=v))
        m = rx(r"annual contribution is \$([\d,]+)").search(text)
        if m:
            v = float(m.group(1).replace(",", ""))
            facts.append(_fact("contingency_reserve", "crf_annual_contribution", f"Annual contribution ${m.group(1)}", _quote(text, "annual contribution"), pno,
                               topic="contingency reserve contribution", date=rdate, amin=v, amax=v))
        m = rx(r"roof[^.]*replacement is recommended in (\d{4}) at an estimated \$([\d,]+)").search(text)
        if m:
            v = float(m.group(2).replace(",", ""))
            facts.append(_fact("special_levies", "major_work_discussed", f"Roof membrane replacement recommended in {m.group(1)} at an estimated ${m.group(2)} (2022 dollars)",
                               _quote(text, "full replacement is recommended"), pno, topic="roof membrane replacement", status="recommended", date=rdate,
                               amin=v, amax=v, data={"year": m.group(1)}))
        if "sealant" in low and "remediation" in low:
            done = "has not yet been carried out" not in low and "not yet" not in low
            facts.append(_fact("building_envelope", "envelope_recommendation", "Envelope sealants identified for remediation in the 2019 assessment; report understands work not yet carried out",
                               _quote(text, "sealants"), pno, topic="building envelope sealant remediation", status="completed" if done else "recommended", date=rdate))
        m = rx(r"completed in (\d{4})").search(text) or rx(r"year of construction:\s*(\d{4})").search(text)
        if m:
            facts.append(_fact("building", "year_built", f"Built {m.group(1)}", _quote(text, m.group(0)), pno, data={"value": m.group(1)}))
        m = rx(r"(cast-in-place reinforced concrete|concrete high-rise|wood[- ]frame|concrete)").search(text)
        if m:
            facts.append(_fact("building", "construction", "Concrete construction", _quote(text, m.group(0)), pno, data={"value": "concrete"}))
        m = rx(r"total unit entitlement[^.]*?is ([\d,]+)|total unit entitlement of ([\d,]+)").search(text)
        if m:
            v = (m.group(1) or m.group(2)).replace(",", "")
            facts.append(_fact("building", "total_unit_entitlement", f"Total unit entitlement {v}", _quote(text, "total unit entitlement"), pno, data={"value": v}))
    return {"facts": facts}


def extract_form_b(user: str) -> dict:
    h = _header(user)
    facts = []
    cdate = None
    for _doc, pno, text in split_pages(user):
        low = text.lower()
        m = rx(r"as of the date of this certificate,\s*([0-9]{1,2} [A-Za-z]+ [0-9]{4})").search(text)
        if m:
            cdate = find_date(m.group(1))
            facts.append(_fact("form_b_unit", "form_b_date", f"Form B dated {m.group(1)}", _quote(text, "date of this certificate"), pno, topic="form b", date=cdate))
        m = rx(r"strata plan ([A-Z]{2,4}\s?\d{2,6})").search(text)
        if m:
            facts.append(_fact("building", "strata_plan", f"Strata plan {m.group(1)}", _quote(text, "strata plan"), pno, data={"value": m.group(1).replace(" ", "")}))
        m = rx(r"strata lot (\d+), suite (\d+[a-z]?)").search(text)
        if m:
            facts.append(_fact("building", "strata_lot", f"Strata lot {m.group(1)}", _quote(text, "strata lot"), pno, data={"value": m.group(1)}))
            facts.append(_fact("building", "unit_number", f"Suite {m.group(2)}", _quote(text, "suite"), pno, data={"value": m.group(2)}))
        m = rx(r"(\d+ [A-Za-z]+ (?:Avenue|Street|Drive|Road|Way|Boulevard|Crescent|Place), (?:Burnaby|Vancouver|Richmond|Surrey|Coquitlam|New Westminster|North Vancouver|West Vancouver|Port Moody), BC)\b").search(text)
        if m:
            addr = m.group(1).strip()
            facts.append(_fact("building", "address", addr, _quote(text, addr[:25]), pno, data={"value": addr}))
        m = rx(r"monthly strata fees[^$]*\$([\d,]+\.?\d*)").search(text)
        if m:
            v = float(m.group(1).replace(",", ""))
            facts.append(_fact("form_b_unit", "monthly_fees", f"Monthly strata fees ${m.group(1)}", _quote(text, "monthly strata fees"), pno, topic="strata fees", date=cdate, amin=v, amax=v))
        m = rx(r"amount owing to the strata corporation[^$]*\$([\d,]+\.?\d*)").search(text)
        if m:
            v = float(m.group(1).replace(",", ""))
            facts.append(_fact("form_b_unit", "arrears", f"Amount owing on the strata lot: ${m.group(1)}", _quote(text, "amount owing"), pno,
                               topic="arrears", status="none" if v == 0 else "discussed", date=cdate, amin=v, amax=v))
        if "special levy" in low and "already been approved" in low:
            none = re.search(r"already been approved[^:]*:\s*none", low) is not None
            facts.append(_fact("special_levies", "levy_none_stated" if none else "levy_passed", "Form B: no special levy approved" if none else "Form B lists an approved levy",
                               _quote(text, "special levy that has already"), pno, topic="approved levies", status="none" if none else "approved", date=cdate))
        m = rx(r"contingency reserve fund[^$]*\$([\d,]+\.?\d*)(?: as at ([0-9]{1,2} [A-Za-z]+ [0-9]{4}))?").search(text)
        if m:
            v = float(m.group(1).replace(",", ""))
            facts.append(_fact("contingency_reserve", "crf_balance", f"Form B contingency reserve balance ${m.group(1)}" + (f" as at {m.group(2)}" if m.group(2) else ""),
                               _quote(text, "contingency reserve fund"), pno, topic="contingency reserve balance",
                               date=find_date(m.group(2)) if m.group(2) else cdate, amin=v, amax=v))
        m = rx(r"resolution that has not been voted on[^:]*:\s*(.{20,400})", re.I | re.S).search(text)
        if m and "levy" in m.group(1).lower()[:300]:
            facts.append(_fact("special_levies", "levy_proposed", "Form B: notice that a special levy resolution for roof replacement is expected at the October 2026 AGM",
                               _quote(text, "special levy resolution"), pno, topic="roof membrane replacement", status="proposed", date=cdate))
        m = rx(r"court proceeding[^:]*:\s*(none|no)\b").search(text)
        if m:
            facts.append(_fact("litigation", "litigation_none_stated", "Form B: no court proceedings, arbitration or tribunal claims", _quote(text, "court proceeding"), pno,
                               topic="litigation", status="none", date=cdate))
        elif "court proceeding" in low:
            facts.append(_fact("litigation", "litigation_active", "Form B lists a court proceeding or tribunal claim", _quote(text, "court proceeding"), pno,
                               topic="litigation", status="discussed", date=cdate))
        m = rx(r"parking stall[s]?[^.]*?(limited common property|common property|leased|not designated)").search(text)
        if m:
            des = m.group(1).lower()
            facts.append(_fact("parking_storage", "parking_designation", f"Parking: {m.group(0)[:120]}", _quote(text, "parking stall"), pno,
                               topic="parking", date=cdate, data={"designation": "lcp" if "limited" in des else ("cp" if "common" in des else des)}))
        m = rx(r"storage locker \d+[^.]*?(limited common property|common property|leased|not designated)").search(text)
        if m:
            des = m.group(1).lower()
            facts.append(_fact("parking_storage", "storage_designation", f"Storage: {m.group(0)[:120]}", _quote(text, "storage locker"), pno,
                               topic="storage", date=cdate, data={"designation": "lcp" if "limited" in des else ("cp" if "common" in des else des)}))
        m = rx(r"unit entitlement of the strata lot:\s*([\d,]+)").search(text)
        if m:
            facts.append(_fact("building", "unit_entitlement", f"Unit entitlement {m.group(1)}", _quote(text, "unit entitlement of the strata lot"), pno, data={"value": m.group(1).replace(",", "")}))
        m = rx(r"strata lots in the strata plan that are rented:\s*(\d+) of (\d+)").search(text)
        if m:
            facts.append(_fact("bylaws", "rentals_count", f"{m.group(1)} of {m.group(2)} strata lots are rented", _quote(text, "rented"), pno,
                               topic="rentals", status="discussed", date=cdate, data={"rented": m.group(1), "lots": m.group(2)}))
    return {"facts": facts}


def extract_financials(user: str) -> dict:
    facts = []
    fy = None
    for _doc, pno, text in split_pages(user):
        m = rx(r"year ended (\d{1,2} [A-Za-z]+ \d{4})").search(text)
        if m and not fy:
            fy = find_date(m.group(1))
        m = rx(r"closing balance,?\s*(\d{1,2} [A-Za-z]+ \d{4}):\s*\$([\d,]+)").search(text)
        if m:
            v = float(m.group(2).replace(",", ""))
            facts.append(_fact("contingency_reserve", "crf_balance", f"Contingency reserve fund closing balance ${m.group(2)} at {m.group(1)}",
                               _quote(text, "closing balance"), pno, topic="contingency reserve balance", date=find_date(m.group(1)), amin=v, amax=v))
        m = rx(r"contribution from operating fund:\s*\$([\d,]+)").search(text)
        if m:
            v = float(m.group(1).replace(",", ""))
            facts.append(_fact("contingency_reserve", "crf_annual_contribution", f"Contribution to reserve ${m.group(1)}", _quote(text, "contribution from operating"), pno,
                               topic="contingency reserve contribution", date=fy, amin=v, amax=v))
        m = rx(r"recommended a balance of \$([\d,]+) by (\d{4})").search(text)
        if m:
            v = float(m.group(1).replace(",", ""))
            facts.append(_fact("contingency_reserve", "crf_recommended_balance", f"Depreciation report recommendation quoted: ${m.group(1)} by {m.group(2)}",
                               _quote(text, "recommended a balance"), pno, topic="contingency reserve balance", status="recommended", date=fy, amin=v, amax=v, data={"target_year": m.group(2)}))
        if rx(r"no special lev(y|ies) (was|were) approved").search(text):
            facts.append(_fact("special_levies", "levy_none_stated", "Financial statements: no special levies approved during the year", _quote(text, "no special lev"), pno,
                               topic="approved levies", status="none", date=fy))
        if rx(r"not party to any legal proceedings").search(text):
            facts.append(_fact("litigation", "litigation_none_stated", "Financial statements note: not party to any legal proceedings", _quote(text, "not party"), pno,
                               topic="litigation", status="none", date=fy))
        m = rx(r"total unit entitlement[^.]*?is ([\d,]+)").search(text)
        if m:
            facts.append(_fact("building", "total_unit_entitlement", f"Total unit entitlement {m.group(1)}", _quote(text, "total unit entitlement"), pno, data={"value": m.group(1).replace(",", "")}))
    return {"facts": facts}


def extract_insurance(user: str) -> dict:
    facts = []
    pdate = None
    for _doc, pno, text in split_pages(user):
        m = rx(r"policy period:\s*(\d{1,2} [A-Za-z]+ \d{4}) to (\d{1,2} [A-Za-z]+ \d{4})").search(text)
        if m:
            pdate = find_date(m.group(1))
        for line in text.splitlines():
            m = re.match(r"^(Water damage[^$]*|Sewer backup|Flood|All other perils|Equipment breakdown|Earthquake)\s+(\$[\d,]+|\d+% of total insured value)$", line.strip(), re.I)
            if m:
                peril = m.group(1).split("(")[0].strip().lower()
                amt = money(m.group(2))
                facts.append(_fact("insurance_deductibles", "deductible", f"{m.group(1).strip()} deductible {m.group(2)}", line.strip(), pno,
                                   topic=f"{peril} deductible", date=pdate, amin=amt[0] if amt else None, amax=amt[0] if amt else None, data={"peril": peril}))
        m = rx(r"water damage deductible increased from \$([\d,]+) to \$([\d,]+)").search(text)
        if m:
            facts.append(_fact("insurance_deductibles", "deductible_change", f"Water damage deductible increased from ${m.group(1)} to ${m.group(2)}", _quote(text, "increased from"), pno,
                               topic="water damage deductible", date=pdate, amin=float(m.group(1).replace(",", "")), amax=float(m.group(2).replace(",", "")), data={"peril": "water damage"}))
    return {"facts": facts}


def extract_engineering(user: str) -> dict:
    facts = []
    rdate = None
    for _doc, pno, text in split_pages(user):
        low = text.lower()
        m = rx(r"report date:\s*(\d{1,2} [A-Za-z]+ \d{4})").search(text)
        if m and not rdate:
            rdate = find_date(m.group(1))
        if "sealant" in low and ("failure" in low or "debonded" in low):
            facts.append(_fact("building_envelope", "envelope_finding", "Perimeter sealant adhesive failure on north and west elevations", _quote(text, "sealant"), pno,
                               topic="building envelope sealant remediation", status="discussed", date=rdate))
        if "water ingress" in low and "suite" in low:
            facts.append(_fact("building_envelope", "water_ingress", "Water ingress reported at suites on the north elevation", _quote(text, "water ingress"), pno,
                               topic="water ingress north elevation", status="discussed", date=rdate))
        if "we recommend" in low and ("sealant" in low or "remediation" in low):
            amts = [a for a in money(text) if a >= 10000]
            m = rx(r"within (\w+) years[^.]*?by (\d{4})").search(text)
            facts.append(_fact("building_envelope", "envelope_recommendation", "Full sealant replacement on north and west elevations recommended within five years (by 2024)",
                               _quote(text, "we recommend"), pno, topic="building envelope sealant remediation", status="recommended", date=rdate,
                               amin=min(amts) if amts else None, amax=max(amts) if amts else None,
                               data={"timeframe": m.group(0) if m else "", "deadline_year": m.group(2) if m else ""}))
    return {"facts": facts}


def extract_bylaws(user: str) -> dict:
    facts = []
    for _doc, pno, text in split_pages(user):
        for m in re.finditer(r"^(\d{1,3})\.\s+(.{20,}?)(?=\n\d{1,3}\.\s|\n\n|\Z)", text, re.M | re.S):
            sec, body = m.group(1), m.group(2)
            low = body.lower()
            q = body.strip()[:180]
            if "rental" in low and ("limited to" in low or "restrict" in low):
                facts.append(_fact("bylaws", "rental_restriction", f"Bylaw {sec}: rental restriction ({body[:100].strip()})", q, pno,
                                   topic="rental restriction bylaw 41", status="discussed", data={"section": sec}))
            elif "short-term" in low or "short term" in low:
                facts.append(_fact("bylaws", "short_term_rental_restriction", f"Bylaw {sec}: short-term accommodation prohibited", q, pno, topic="short-term rentals", status="discussed", data={"section": sec}))
            elif "pets" in low or "dogs" in low:
                facts.append(_fact("bylaws", "pet_restriction", f"Bylaw {sec}: pet limits", q, pno, topic="pets", status="discussed", data={"section": sec}))
            elif "age of occupants" in low:
                none = "no restriction" in low
                facts.append(_fact("bylaws", "age_restriction", f"Bylaw {sec}: " + ("no age restriction" if none else "age restriction"), q, pno, topic="age restriction",
                                   status="none" if none else "discussed", data={"section": sec}))
            elif "deductible" in low:
                facts.append(_fact("insurance_deductibles", "deductible_chargeback_bylaw", f"Bylaw {sec}: owner responsible for deductible where loss originates in their lot", q, pno,
                                   topic="water damage deductible", status="discussed", data={"section": sec}))
            elif "storage locker" in low or "parking stall" in low:
                facts.append(_fact("parking_storage", "storage_designation", f"Bylaw {sec}: storage lockers are common property allocated by council; LCP stalls exclusive", q, pno,
                                   topic="storage", status="discussed", data={"section": sec, "designation": "cp"}))
    return {"facts": facts}


HANDLERS.update({
    "extract_council_minutes": extract_minutes,
    "extract_agm_sgm_minutes": extract_minutes,
    "extract_depreciation_report": extract_depreciation,
    "extract_form_b": extract_form_b,
    "extract_financial_statements": extract_financials,
    "extract_insurance_summary": extract_insurance,
    "extract_engineering_report": extract_engineering,
    "extract_bylaws": extract_bylaws,
})


# ---- severity judgment ------------------------------------------------------------------
# The mock agrees with the rule severity and returns no narrative, so judge.py uses the
# deterministic template text. Disagreement paths are exercised in tests with a fake client.

def assign_severity(user: str) -> dict:
    import json
    payload = json.loads(user.split("THREADS:\n", 1)[1])
    js = [{"thread_id": t["thread_id"], "severity": t["rule_severity"] or "note", "rationale": "Mock: rule severity applied as-is.",
           "title": "", "agent_text": "", "client_title": "", "client_text": ""} for t in payload["threads"]]
    return {"judgments": js, "questions": []}


HANDLERS["assign_severity"] = assign_severity
