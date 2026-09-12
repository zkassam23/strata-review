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
