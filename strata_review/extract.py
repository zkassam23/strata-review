"""Stage 3: extract typed facts from each section, then enforce the page-anchor rule.

Anchor rule (enforced here, not in prompts):
  * a fact must cite at least one page number that was in the pages sent for that call;
  * its verbatim quote must be found on one of the cited pages (exact after whitespace
    normalisation, or a fuzzy match on OCR'd pages);
  * its (category, kind) pair must be allowed for the section's document type per the taxonomy.
Anything failing is logged to the discard list with the reason and never becomes a Fact.

Absence facts: for each taxonomy document type with no section in the package, one Fact of
kind document_absent is generated in code with an "absence" citation naming the package. These
are the only facts allowed to carry a citation without a page number.
"""
from __future__ import annotations

import difflib
import logging
import re
from datetime import date
from typing import Any, Callable

from . import prompts
from .llm import LLM
from .schemas import Citation, DiscardedFact, Fact, Page, Section, SourceDoc, Usage

log = logging.getLogger(__name__)

MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DOC_LABELS = {
    "council_minutes": "Council minutes", "agm_sgm_minutes": "General meeting minutes",
    "depreciation_report": "Depreciation report", "form_b": "Form B", "financial_statements": "Financial statements",
    "insurance_summary": "Insurance summary", "engineering_report": "Engineering report", "bylaws": "Bylaws", "other": "Document",
}
CLIENT_DOC_LABELS = {
    "council_minutes": "Council meeting notes", "agm_sgm_minutes": "Owners' meeting notes",
    "depreciation_report": "Depreciation report", "form_b": "Form B certificate", "financial_statements": "Financial statements",
    "insurance_summary": "Insurance summary", "engineering_report": "Engineer's assessment", "bylaws": "Bylaws", "other": "Document",
}


def _fmt_date(iso: str | None, short: bool = True) -> str:
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{d.day} {MONTHS_SHORT[d.month - 1]} {d.year}" if short else f"{d.strftime('%B')} {d.year}"


def cite_label(section: Section, page: int, section_date: str | None = None) -> str:
    """Agent-facing citation: 'Council minutes, 14 Apr 2026, p. 3'."""
    when = _fmt_date(section.meeting_date or section_date)
    base = DOC_LABELS.get(section.doc_type, "Document")
    return f"{base}, {when}, p. {page}" if when else f"{base}, p. {page}"


def client_cite_label(section: Section, section_date: str | None = None) -> str:
    when = _fmt_date(section.meeting_date or section_date, short=False)
    base = CLIENT_DOC_LABELS.get(section.doc_type, "Document")
    return f"{base}, {when}" if when else base


def allowed_pairs(taxonomy: dict[str, Any], doc_type: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for cat, spec in taxonomy["categories"].items():
        if doc_type in spec.get("doc_types", []):
            pairs += [(cat, k) for k in spec.get("fact_kinds", [])]
    meta = taxonomy.get("metadata", {})
    if doc_type in meta.get("doc_types", []):
        pairs += [(meta["category"], k) for k in meta.get("fact_kinds", [])]
    return pairs


_ws = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _ws.sub(" ", s.replace("’", "'").replace("–", "-").replace("—", "-")).strip().lower()


def quote_found(quote: str, page: Page, fuzzy_ratio: float = 0.75) -> bool:
    """Exact substring after whitespace normalisation; otherwise longest-common-run fuzzy match
    (needed for OCR'd pages, where a few characters drift)."""
    q, t = _norm(quote), _norm(page.text)
    if not q:
        return False
    if q in t:
        return True
    if len(q) < 12:
        return False
    sm = difflib.SequenceMatcher(None, q, t, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks() if b.size >= 4)
    return matched / len(q) >= fuzzy_ratio


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def validate_fact(raw: dict[str, Any], section: Section, pages_sent: list[Page], allowed: set[tuple[str, str]],
                  fact_id: str) -> tuple[Fact | None, str | None]:
    """Apply the anchor rule. Returns (fact, None) or (None, reason)."""
    cat, kind = str(raw.get("category", "")), str(raw.get("kind", ""))
    if (cat, kind) not in allowed:
        return None, f"kind not allowed for {section.doc_type}: {cat}/{kind}"
    summary = _ws.sub(" ", str(raw.get("summary") or "")).strip()
    if not summary:
        return None, "empty summary"
    by_num = {p.page_number: p for p in pages_sent}
    cited = [int(n) for n in (raw.get("page_numbers") or []) if isinstance(n, (int, float))]
    if not cited:
        return None, "no page anchor"
    bad = [n for n in cited if n not in by_num]
    if bad:
        return None, f"cited page(s) {bad} not among pages sent ({pages_sent[0].page_number}-{pages_sent[-1].page_number})"
    quote = str(raw.get("quote") or "")
    anchored = [n for n in cited if quote_found(quote, by_num[n])]
    if not anchored:
        return None, f"quote not found on cited page(s) {cited}: {quote[:80]!r}"
    status = raw.get("status")
    if status not in prompts.STATUS_LIST:
        status = None
    data = {}
    for kv in raw.get("data") or []:
        if isinstance(kv, dict) and "key" in kv:
            data[str(kv["key"])] = str(kv.get("value", ""))
    data["quote"] = quote
    amin, amax = raw.get("amount_min"), raw.get("amount_max")
    if amin is not None and amax is not None and amin > amax:
        amin, amax = amax, amin
    fact_date = raw.get("date") or section.meeting_date
    cites = [Citation(source_doc=section.source_file, page_number=n, label=cite_label(section, n, fact_date))
             for n in anchored]
    return Fact(
        fact_id=fact_id, category=cat, kind=kind, summary=summary, section_id=section.section_id,
        doc_type=section.doc_type, citations=cites, date=fact_date, topic=(raw.get("topic") or None),
        status=status, amount_min=amin, amount_max=amax, data=data,
    ), None


def extract_section(section: Section, doc: SourceDoc, llm: LLM, model: str, taxonomy: dict[str, Any], *,
                    unit: str | None, pages_per_call: int, counter: list[int]) -> tuple[list[Fact], list[DiscardedFact], list[Usage]]:
    pairs = allowed_pairs(taxonomy, section.doc_type)
    allowed = set(pairs)
    system = prompts.extract_system(section.doc_type, pairs)
    pages = [p for p in doc.pages if section.page_start <= p.page_number <= section.page_end]
    facts, discards, usages = [], [], []
    for chunk in _chunks(pages, pages_per_call):
        user = prompts.extract_user(section, chunk, unit)
        payload, usage = llm.complete_json(task=f"extract_{section.doc_type}", model=model, system=system, user=user,
                                           schema=prompts.EXTRACT_SCHEMA, max_tokens=16000)
        usages.append(usage)
        for raw in payload.get("facts", []):
            counter[0] += 1
            fact, reason = validate_fact(raw, section, chunk, allowed, f"f{counter[0]:04d}")
            if fact is None:
                log.info("discard %s %s: %s", section.section_id, reason, raw.get("summary", "")[:60])
                discards.append(DiscardedFact(section_id=section.section_id, reason=reason, payload=raw))
            else:
                facts.append(fact)
    return facts, discards, usages


def absence_facts(sections: list[Section], docs: list[SourceDoc], taxonomy: dict[str, Any], counter: list[int]) -> list[Fact]:
    present = {s.doc_type for s in sections}
    out = []
    names = ", ".join(d.source_file for d in docs)
    for dt, label in taxonomy["doc_types"].items():
        if dt == "other" or dt in present:
            continue
        counter[0] += 1
        out.append(Fact(
            fact_id=f"f{counter[0]:04d}", category=taxonomy["absence"]["category"], kind=taxonomy["absence"]["fact_kind"],
            summary=f"No {label.lower()} was supplied in this package.", section_id="", doc_type=dt,
            citations=[Citation(source_doc="(package)", page_number=None, kind="absence",
                                label=f"Absent from the {len(docs)} documents supplied: {label.lower()}")],
            status="none", data={"doc_type": dt, "searched": names},
        ))
    return out


def extract(sections: list[Section], docs: list[SourceDoc], llm: LLM, model: str, taxonomy: dict[str, Any], *,
            unit: str | None = None, pages_per_call: int = 12,
            progress: Callable[[str], None] | None = None) -> tuple[list[Fact], list[DiscardedFact], list[Usage]]:
    by_doc = {d.doc_id: d for d in docs}
    facts, discards, usages = [], [], []
    counter = [0]
    for s in sections:
        if s.doc_type == "other":
            log.info("skipping %s (%s p.%s-%s): type other", s.section_id, s.source_file, s.page_start, s.page_end)
            continue
        if progress:
            progress(f"Extracting {s.doc_type} {s.source_file} p.{s.page_start}-{s.page_end}")
        f, d, u = extract_section(s, by_doc[s.doc_id], llm, model, taxonomy, unit=unit, pages_per_call=pages_per_call, counter=counter)
        facts += f
        discards += d
        usages += u
    facts += absence_facts(sections, docs, taxonomy, counter)
    return facts, discards, usages


def building_metadata(facts: list[Fact], taxonomy: dict[str, Any]) -> dict[str, Any]:
    """Collapse category-building facts into one dict; first occurrence wins, Form B preferred."""
    meta_cat = taxonomy.get("metadata", {}).get("category", "building")
    out: dict[str, Any] = {}
    ordered = sorted((f for f in facts if f.category == meta_cat), key=lambda f: 0 if f.doc_type == "form_b" else 1)
    for f in ordered:
        val = f.data.get("value") or f.summary
        out.setdefault(f.kind, val)
        out.setdefault(f"{f.kind}_cite", f.citations[0].label)
    return out
