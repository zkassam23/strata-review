from pathlib import Path

from strata_review.ingest import collect_pdfs, ingest, summarise


def test_folder_ingest_detects_scanned_pages_and_ocrs_them(tiny_package: Path):
    docs = ingest(tiny_package, ocr_threshold=70, min_chars=40)
    assert [d.source_file for d in docs] == ["formb.pdf", "minutes.pdf"]
    minutes = docs[1]
    assert minutes.n_pages == 2
    p1, p2 = minutes.pages
    assert not p1.is_scanned and p1.ocr_confidence is None
    assert "$780,000" in p1.text
    assert p2.is_scanned and p2.ocr_confidence is not None
    assert "Landscaping" in p2.text
    assert p2.page_number == 2 and p2.doc_id == minutes.doc_id


def test_ocr_confidence_is_recorded_and_thresholded(tiny_package: Path):
    docs = ingest(tiny_package, ocr_threshold=99.9, min_chars=40)   # absurd threshold: clean scan still flagged
    scanned = [p for d in docs for p in d.pages if p.is_scanned]
    assert len(scanned) == 1
    assert scanned[0].low_ocr is True
    docs = ingest(tiny_package, ocr_threshold=50, min_chars=40)
    scanned = [p for d in docs for p in d.pages if p.is_scanned]
    assert scanned[0].low_ocr is False
    s = summarise(docs)
    assert (s.n_docs, s.n_pages, s.n_scanned, s.n_low_ocr) == (2, 3, 1, 0)


def test_zip_and_single_pdf_inputs(tiny_zip: Path, tiny_package: Path, tmp_path: Path):
    pdfs = collect_pdfs(tiny_zip, workdir=tmp_path / "unz")
    assert [p.name for p in pdfs] == ["formb.pdf", "minutes.pdf"]
    docs = ingest(tiny_zip, workdir=tmp_path / "unz2")
    assert sum(d.n_pages for d in docs) == 3
    single = ingest(tiny_package / "formb.pdf")
    assert len(single) == 1 and single[0].pages[0].text.startswith("FORM B")
