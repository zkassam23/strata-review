"""Prompt text and JSON schemas for each LLM task. Kept in one place so the mock handlers
can parse the same user-message layout the real model receives."""
from __future__ import annotations

PAGE_SEP = "=== PAGE {doc_id}:{page_number} ==="
PAGE_CHARS_FOR_CLASSIFY = 1800

DOC_TYPE_LIST = [
    "council_minutes", "agm_sgm_minutes", "depreciation_report", "form_b",
    "financial_statements", "insurance_summary", "engineering_report", "bylaws", "other",
]

CLASSIFY_SYSTEM = """You classify pages from a British Columbia strata (condo) document package.
For each page decide which document type it belongs to. Pages are given in order from one
file; a file may contain several documents merged together, so type can change part way
through. Use the page content, running headers and footers. Never use the filename.

Types: council_minutes (strata council meeting minutes), agm_sgm_minutes (annual or special
general meeting minutes), depreciation_report, form_b (Form B Information Certificate),
financial_statements (statements, budgets, reserve fund statements), insurance_summary
(policy summary or certificate), engineering_report (building envelope, roof, structural or
condition assessment), bylaws (bylaws or rules), other.

Give a confidence between 0 and 1 that reflects how clearly the page shows its type. A
continuation page with little context should get a lower confidence, not a guess dressed as
certainty. If the page is part of a dated meeting's minutes, give meeting_date as YYYY-MM-DD.
Give a short title when a document title or heading is visible."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_number": {"type": "integer"},
                    "doc_type": {"type": "string", "enum": DOC_TYPE_LIST},
                    "confidence": {"type": "number"},
                    "meeting_date": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                },
                "required": ["page_number", "doc_type", "confidence", "meeting_date", "title"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pages"],
    "additionalProperties": False,
}


def render_pages(pages, max_chars: int | None = None) -> str:
    parts = []
    for p in pages:
        text = p.text if max_chars is None else p.text[:max_chars]
        parts.append(PAGE_SEP.format(doc_id=p.doc_id, page_number=p.page_number) + "\n" + text)
    return "\n\n".join(parts)


# ---- extraction ------------------------------------------------------------------------

STATUS_LIST = ["discussed", "quotes_requested", "quotes_received", "proposed", "deferred", "approved",
               "tendered", "completed", "recommended", "none"]

EXTRACT_SYSTEM_BASE = """You extract structured facts from one section of a British Columbia strata
(condo) document package for a buyer's due diligence review. You will be given the pages of one
section, each page marked "=== PAGE <doc>:<n> ===". Output facts as JSON.

Rules that are enforced in code, so follow them exactly:
1. Every fact must list the page number(s) it comes from, using only the page numbers you were
   given. A fact with no page is discarded.
2. Every fact must include "quote": a short verbatim passage (under 200 characters) copied from the
   cited page that supports the fact. Facts whose quote cannot be found on the cited page are
   discarded. Do not paraphrase inside the quote; copy it.
3. Use only the (category, kind) pairs listed below for this document type. Anything else is
   discarded.
4. Never invent amounts, dates or outcomes. If the document does not state a figure, leave
   amount_min and amount_max null. Amounts are in dollars as numbers, without currency symbols.
5. "topic" is a short noun phrase for what the item is about (e.g. "roof membrane replacement",
   "water damage deductible", "rental restriction bylaw 41"). Use the same wording for the same
   subject wherever it recurs so mentions can be linked across meetings.
6. "status" is the action state for that mention, one of: {statuses}. For minutes items this is
   what happened at that meeting (quotes requested, quotes received, deferred, approved, ...).
   For reports, use "recommended" for recommendations, "none" for statements that something does
   not exist (no arrears, no lawsuits, no levy).
7. "date" is the date the fact speaks as of (ISO YYYY-MM-DD): the meeting date for minutes, the
   report or certificate date for reports, the balance date for a balance. Leave null if unknown.
8. Extract building metadata (category "building") when the page states it: address, strata
   plan number, city, year built, construction type, strata lot, unit entitlement, total unit
   entitlement, unit number. Put the value in data as {{key: "value", value: "..."}}.
9. Extract statements of absence too: "no arrears", "no lawsuits", "no special levy approved" are
   facts with status "none". They matter as much as positive findings.

Allowed (category, kind) pairs for this document type:
{allowed}
"""

EXTRACT_GUIDANCE: dict[str, str] = {
    "council_minutes": """This is one strata council meeting. Extract each agenda item that touches the allowed
categories as its own fact: repairs, quotes, levies, reserve fund balances reported by the
treasurer, insurance renewals and deductibles, bylaw discussions, disputes, legal matters, water
ingress or envelope issues. Record the action status at this meeting precisely. If quotes are
listed, put the lowest in amount_min and the highest in amount_max. Skip routine items
(landscaping, fobs, amenity bookings) unless they carry a material cost.""",
    "agm_sgm_minutes": """This is an annual or special general meeting. Extract resolutions and their outcomes (levies,
bylaw amendments, budgets with the contingency contribution), council reports on major projects,
and any depreciation report, insurance or legal discussion. A levy resolution that CARRIED is
status "approved"; one that failed or was withdrawn is "proposed" with data outcome.""",
    "depreciation_report": """Extract: the report date; the recommended contingency reserve balance and its target year;
the fund balance at the report date; the annual contribution; each major component due within
ten years with its estimated cost and year (as major_work_discussed, status "recommended");
any envelope or water ingress findings; building metadata (year built, construction, total
unit entitlement, strata lots).""",
    "form_b": """This is a Form B Information Certificate for one strata lot. Extract every lettered item that
maps to an allowed kind: monthly fees, amounts owing (arrears; state 0 explicitly with status
"none"), approved levies or their absence, contingency balance, notices of resolutions not yet
voted on (as levy_proposed if about a levy), court or tribunal proceedings or their absence,
rentals count, parking and storage designation (say whether limited common property, common
property allocated by council, leased, or not stated), unit entitlement, strata lot and unit
number, and the certificate date.""",
    "financial_statements": """Extract: fiscal year end; contingency reserve fund closing balance with its date; the annual
contribution to the reserve; receivables or arrears totals; any special levy fund; any recommended
reserve figure quoted; statements about legal proceedings; total unit entitlement if stated.""",
    "insurance_summary": """Extract the policy period, each deductible as its own fact with data peril (water damage,
sewer backup, earthquake, flood, all other perils), any stated change in a deductible, the total
insured value, and any note about owners being responsible for deductibles.""",
    "engineering_report": """Extract the report date, each significant finding (location, issue) as envelope_finding, water
ingress events as water_ingress, each recommendation as envelope_recommendation with the
recommended timeframe in data and the cost opinion in amount_min/amount_max, and any statement
that work has or has not been done.""",
    "bylaws": """Extract each bylaw that restricts rentals, pets, age of occupants, or short-term accommodation,
and any bylaw making owners responsible for insurance deductibles, and any bylaw about parking or
storage allocation. Put the bylaw section number in data as {key: "section", value: "41"}. A bylaw
that states there is no restriction is still a fact, with status "none".""",
}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "kind": {"type": "string"},
                    "summary": {"type": "string"},
                    "quote": {"type": "string"},
                    "page_numbers": {"type": "array", "items": {"type": "integer"}},
                    "topic": {"type": ["string", "null"]},
                    "status": {"type": ["string", "null"], "enum": STATUS_LIST + [None]},
                    "date": {"type": ["string", "null"]},
                    "amount_min": {"type": ["number", "null"]},
                    "amount_max": {"type": ["number", "null"]},
                    "data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                            "required": ["key", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["category", "kind", "summary", "quote", "page_numbers", "topic", "status",
                             "date", "amount_min", "amount_max", "data"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


def extract_system(doc_type: str, allowed_pairs: list[tuple[str, str]]) -> str:
    allowed = "\n".join(f"  - {c} / {k}" for c, k in allowed_pairs)
    return (EXTRACT_SYSTEM_BASE.format(statuses=", ".join(STATUS_LIST), allowed=allowed)
            + "\nDocument-type guidance:\n" + EXTRACT_GUIDANCE.get(doc_type, "Extract whatever maps to the allowed kinds."))


def extract_user(section, pages, unit: str | None) -> str:
    head = [f"SECTION: {section.doc_type}", f"SOURCE FILE: {section.source_file}",
            f"PAGES IN THIS CALL: {pages[0].page_number}-{pages[-1].page_number}"]
    if section.meeting_date:
        head.append(f"MEETING DATE: {section.meeting_date}")
    if unit:
        head.append(f"UNIT UNDER REVIEW: {unit}")
    return "\n".join(head) + "\n\n" + render_pages(pages)
