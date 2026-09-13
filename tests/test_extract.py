"""Extraction on the synthetic package via the mock. Checks the structure threading needs."""
import pytest

from strata_review import pipeline
from strata_review.settings import load_settings


@pytest.fixture(scope="module")
def state(synth_package):
    settings = load_settings(llm_mode="mock")
    settings.extra["unit"] = "1204"
    return pipeline.run(synth_package, settings=settings, stop_after="extract")


def test_reference_case_roof_quotes_then_deferral_extract_linkably(state):
    roof = [f for f in state.facts if f.category == "special_levies" and f.topic == "roof membrane replacement" and f.doc_type == "council_minutes"]
    by_date = {f.date: f for f in roof}
    nov = by_date["2025-11-08"]
    apr = by_date["2026-04-08"]
    assert nov.status == "quotes_received" and (nov.amount_min, nov.amount_max) == (780000, 940000)
    assert apr.status == "deferred" and (apr.amount_min, apr.amount_max) == (780000, 940000)
    assert nov.citations[0].page_number == 29 and apr.citations[0].page_number == 39
    assert nov.topic == apr.topic
    # the September quotes-requested mention is part of the same thread later
    assert by_date["2025-09-09"].status == "quotes_requested"


def test_every_reported_fact_has_a_page_anchor_or_is_an_absence(state):
    for f in state.facts:
        assert f.citations, f.fact_id
        for c in f.citations:
            if c.kind == "page":
                assert c.page_number is not None and c.source_doc.endswith(".pdf")
            else:
                assert f.kind == "document_absent"


def test_no_absences_when_every_document_type_is_supplied(state):
    assert not [f for f in state.facts if f.kind == "document_absent"]


def test_absence_recorded_when_envelope_report_withheld(synth_package, tmp_path):
    import shutil
    sub = tmp_path / "no_envelope"
    sub.mkdir()
    for f in synth_package.glob("*.pdf"):
        if "envelope" not in f.name:
            shutil.copy(f, sub / f.name)
    st = pipeline.run(sub, settings=load_settings(llm_mode="mock"), stop_after="extract")
    absent = [f for f in st.facts if f.kind == "document_absent"]
    assert [f.data["doc_type"] for f in absent] == ["engineering_report"]
    assert absent[0].citations[0].kind == "absence"


def test_key_figures_extracted_with_pages(state):
    kinds = {(f.doc_type, f.kind): f for f in state.facts}
    rec = kinds[("depreciation_report", "crf_recommended_balance")]
    assert rec.amount_min == 1420000 and rec.data["target_year"] == "2027"
    bal = kinds[("financial_statements", "crf_balance")]
    assert bal.amount_min == 298412 and bal.date == "2025-12-31"
    water = next(f for f in state.facts if f.kind == "deductible" and f.data.get("peril") == "water damage" and f.doc_type == "insurance_summary")
    assert water.amount_min == 100000
    envelope = next(f for f in state.facts if f.kind == "envelope_recommendation" and f.doc_type == "engineering_report")
    assert envelope.status == "recommended" and envelope.amount_min == 340000 and envelope.amount_max == 420000
    assert state.building["unit_entitlement"] == "182" and state.building["total_unit_entitlement"] == "10000"
    assert state.building["address"] == "2135 Springer Avenue, Burnaby, BC"
    assert not any(f.category == "litigation" and f.topic == "landscaping" for f in state.facts)
