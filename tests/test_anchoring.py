"""The page-anchor rule, exercised with adversarial payloads shaped like real model output."""
import pytest

from strata_review.extract import absence_facts, allowed_pairs, quote_found, validate_fact
from strata_review.schemas import Page, Section, SourceDoc
from strata_review.settings import load_settings

TAX = load_settings(llm_mode="mock").taxonomy
SEC = Section(section_id="s001", doc_id="d01", source_file="minutes.pdf", doc_type="council_minutes",
              page_start=3, page_end=4, confidence=0.9, meeting_date="2026-04-08")
P3 = Page(doc_id="d01", source_file="minutes.pdf", page_number=3,
          text="4. Roof replacement, decision deferred\nThe third quote, from Summit Roofing at $850,000 plus GST,\nwas received. Council resolved to defer the decision to the annual general meeting.")
P4 = Page(doc_id="d01", source_file="minutes.pdf", page_number=4, text="5. Landscaping. The cedars will be replaced.", is_scanned=True,
          text_override=None) if False else Page(doc_id="d01", source_file="minutes.pdf", page_number=4,
          text="5. Landscaping. The cedars wiIl be repIaced.", is_scanned=True, ocr_confidence=88.0)
ALLOWED = set(allowed_pairs(TAX, "council_minutes"))


def _raw(**over):
    base = {"category": "special_levies", "kind": "work_deferred", "summary": "Roof decision deferred to the AGM",
            "quote": "Council resolved to defer the decision to the annual general meeting", "page_numbers": [3],
            "topic": "roof membrane replacement", "status": "deferred", "date": None, "amount_min": 850000, "amount_max": 850000,
            "data": [{"key": "heading", "value": "Roof replacement"}]}
    base.update(over)
    return base


def test_well_anchored_fact_is_kept_with_citation_label():
    fact, reason = validate_fact(_raw(), SEC, [P3, P4], ALLOWED, "f0001")
    assert reason is None
    assert fact.citations[0].page_number == 3 and fact.citations[0].source_doc == "minutes.pdf"
    assert fact.citations[0].label == "Council minutes, 8 Apr 2026, p. 3"
    assert fact.date == "2026-04-08" and fact.status == "deferred" and fact.data["heading"] == "Roof replacement"


@pytest.mark.parametrize("override, reason_prefix", [
    ({"page_numbers": []}, "no page anchor"),
    ({"page_numbers": [7]}, "cited page(s) [7] not among pages sent"),
    ({"quote": "The council deferred the roof choice until next year"}, "quote not found"),   # paraphrase, not verbatim
    ({"quote": "Council resolved to defer the decision", "page_numbers": [4]}, "quote not found"),  # right words, wrong page
    ({"category": "special_levies", "kind": "deductible"}, "kind not allowed"),
    ({"category": "insurance_deductibles", "kind": "envelope_finding"}, "kind not allowed"),
    ({"summary": "  "}, "empty summary"),
])
def test_unanchored_or_disallowed_facts_are_discarded(override, reason_prefix):
    fact, reason = validate_fact(_raw(**override), SEC, [P3, P4], ALLOWED, "f0002")
    assert fact is None
    assert reason.startswith(reason_prefix), reason


def test_multi_page_citation_keeps_only_pages_where_quote_is_found():
    fact, reason = validate_fact(_raw(page_numbers=[3, 4]), SEC, [P3, P4], ALLOWED, "f0003")
    assert reason is None and [c.page_number for c in fact.citations] == [3]


def test_quote_matching_tolerates_line_wraps_and_ocr_drift():
    assert quote_found("from Summit Roofing at $850,000 plus GST, was received", P3)      # wrapped across a newline
    assert quote_found("The cedars will be replaced", P4)                                # OCR read l as I
    assert not quote_found("The cedars will be planted in spring", P4)
    assert not quote_found("", P3)


def test_absence_facts_are_generated_for_missing_document_types():
    docs = [SourceDoc(doc_id="d01", source_file="minutes.pdf", path="", n_pages=2)]
    facts = absence_facts([SEC], docs, TAX, counter=[0])
    kinds = {f.data["doc_type"] for f in facts}
    assert "engineering_report" in kinds and "form_b" in kinds and "council_minutes" not in kinds and "other" not in kinds
    eng = next(f for f in facts if f.data["doc_type"] == "engineering_report")
    assert eng.kind == "document_absent" and eng.citations[0].kind == "absence" and eng.citations[0].page_number is None
    assert "minutes.pdf" in eng.data["searched"]
