from strata_review.classify import build_sections, classify, smooth
from strata_review.llm import MockLLM
from strata_review.schemas import Page, PageClassification, SourceDoc


def _doc(doc_id: str, n: int) -> SourceDoc:
    pages = [Page(doc_id=doc_id, source_file=f"{doc_id}.pdf", page_number=i, text="x") for i in range(1, n + 1)]
    return SourceDoc(doc_id=doc_id, source_file=f"{doc_id}.pdf", path="", n_pages=n, pages=pages)


def _cls(doc_id, page, dt, conf=0.9, date=None):
    return PageClassification(doc_id=doc_id, page_number=page, doc_type=dt, confidence=conf, meeting_date=date)


def test_sections_split_by_type_and_meeting_date_not_filename():
    doc = _doc("d01", 7)
    cls = [
        _cls("d01", 1, "council_minutes", date="2025-11-08"),
        _cls("d01", 2, "council_minutes"),                       # undated continuation stays
        _cls("d01", 3, "council_minutes", conf=0.3),              # blank scan, undated, stays with meeting 1
        _cls("d01", 4, "council_minutes", date="2026-04-08"),     # new meeting
        _cls("d01", 5, "council_minutes"),
        _cls("d01", 6, "form_b"),                                 # type change
        _cls("d01", 7, "form_b"),
    ]
    secs = build_sections([doc], cls, threshold=0.7)
    assert [(s.doc_type, s.page_start, s.page_end, s.meeting_date) for s in secs] == [
        ("council_minutes", 1, 3, "2025-11-08"),
        ("council_minutes", 4, 5, "2026-04-08"),
        ("form_b", 6, 7, None),
    ]
    assert secs[0].low_confidence is False and secs[0].confidence == round((0.9 + 0.9 + 0.3) / 3, 3)


def test_low_confidence_section_is_surfaced_not_resolved():
    doc = _doc("d01", 2)
    secs = build_sections([doc], [_cls("d01", 1, "other", 0.4), _cls("d01", 2, "other", 0.5)], threshold=0.7)
    assert len(secs) == 1 and secs[0].low_confidence is True and secs[0].doc_type == "other"


def test_smoothing_only_relabels_lone_low_confidence_pages():
    cls = [_cls("d01", 1, "bylaws"), _cls("d01", 2, "other", 0.4), _cls("d01", 3, "bylaws"),
           _cls("d01", 4, "form_b", 0.95), _cls("d01", 5, "bylaws")]
    out = smooth(cls, threshold=0.7)
    assert out[1].doc_type == "bylaws" and out[1].smoothed_from == "other"
    assert out[3].doc_type == "form_b" and out[3].smoothed_from is None   # confident page is left alone


def test_mock_classifier_types_pages_and_reads_meeting_dates(tiny_package):
    from strata_review.ingest import ingest
    docs = ingest(tiny_package)
    secs, cls, usage = classify(docs, MockLLM(), "claude-haiku-4-5", threshold=0.7, batch_size=8)
    by_file = {s.source_file: s for s in secs}
    assert by_file["formb.pdf"].doc_type == "form_b"
    assert by_file["minutes.pdf"].doc_type == "council_minutes"
    assert by_file["minutes.pdf"].meeting_date == "2026-04-14"
    assert by_file["minutes.pdf"].page_end == 2      # OCR'd continuation page joined the meeting
    assert all(u.mocked for u in usage) and len(usage) == 2


def test_merged_pdf_is_split_into_typed_sections_by_content(synth_package):
    from strata_review.ingest import ingest
    merged = synth_package.parent / "package-merged.pdf"
    docs = ingest(merged, ocr_threshold=70, min_chars=40)
    assert len(docs) == 1
    secs, _, _ = classify(docs, MockLLM(), "claude-haiku-4-5", threshold=0.7, batch_size=8)
    types = [s.doc_type for s in secs]
    assert types.count("council_minutes") == 24                   # one section per meeting
    for t in ("agm_sgm_minutes", "depreciation_report", "form_b", "financial_statements",
              "insurance_summary", "engineering_report", "bylaws"):
        assert types.count(t) == 1, t
    dates = [s.meeting_date for s in secs if s.doc_type == "council_minutes"]
    assert dates == sorted(dates) and len(set(dates)) == 24
