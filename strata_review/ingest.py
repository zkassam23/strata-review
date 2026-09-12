"""Stage 1: ingest a folder of PDFs, a single PDF, or a zip into pages of text.

Scanned pages (fewer than ocr_min_chars_per_page extractable characters) are rendered and
OCR'd with tesseract. Mean word confidence is recorded per page; pages under the OCR
threshold are marked low_ocr and later listed in the report appendix.
"""
from __future__ import annotations

import io
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Iterable

import pdfplumber

from .schemas import Page, PackageSummary, SourceDoc

log = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


def collect_pdfs(path: Path, workdir: Path | None = None) -> list[Path]:
    """Resolve the input into a sorted list of PDF paths. Zips are extracted into workdir."""
    path = Path(path)
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix.lower() == ".pdf" and not p.name.startswith("."))
    if path.suffix.lower() == ".pdf":
        return [path]
    if path.suffix.lower() == ".zip":
        workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="strata-zip-"))
        workdir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            for member in zf.infolist():
                name = Path(member.filename).name
                if member.is_dir() or not name.lower().endswith(".pdf") or name.startswith("."):
                    continue
                target = workdir / name
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        return sorted(workdir.glob("*.pdf"))
    raise ValueError(f"Unsupported input: {path} (expected a folder, a .pdf, or a .zip)")


def _ocr_page(page: "pdfplumber.page.Page", dpi: int = 220) -> tuple[str, float | None]:
    """Render a page and OCR it. Returns (text, mean word confidence 0-100 or None if no words)."""
    import pytesseract

    img = page.to_image(resolution=dpi).original.convert("L")
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    lines: list[str] = []
    confs: list[float] = []
    current_key, current_words = None, []
    for txt, conf, blk, par, ln in zip(data["text"], data["conf"], data["block_num"], data["par_num"], data["line_num"]):
        try:
            c = float(conf)
        except (TypeError, ValueError):
            continue
        if c < 0 or not str(txt).strip():
            continue
        key = (blk, par, ln)
        if key != current_key:
            if current_words:
                lines.append(" ".join(current_words))
                if current_key and current_key[:2] != key[:2]:
                    lines.append("")
            current_key, current_words = key, []
        current_words.append(str(txt))
        confs.append(c)
    if current_words:
        lines.append(" ".join(current_words))
    mean_conf = sum(confs) / len(confs) if confs else None
    return _normalise("\n".join(lines)), mean_conf


def _normalise(text: str) -> str:
    text = text.replace("\x0c", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ingest_pdf(pdf_path: Path, doc_id: str, *, ocr_threshold: float, min_chars: int,
               progress: ProgressFn | None = None) -> SourceDoc:
    pages: list[Page] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        n = len(pdf.pages)
        for i, p in enumerate(pdf.pages, start=1):
            text = _normalise(p.extract_text() or "")
            page = Page(doc_id=doc_id, source_file=pdf_path.name, page_number=i, text=text)
            if len(text) < min_chars:
                if progress:
                    progress(f"OCR {pdf_path.name} p.{i}/{n}")
                ocr_text, conf = _ocr_page(p)
                page.is_scanned = True
                page.text = ocr_text
                page.ocr_confidence = round(conf, 1) if conf is not None else None
                page.low_ocr = conf is None or conf < ocr_threshold
            pages.append(page)
    return SourceDoc(doc_id=doc_id, source_file=pdf_path.name, path=str(pdf_path), n_pages=len(pages), pages=pages)


def ingest(path: Path, *, ocr_threshold: float = 70, min_chars: int = 40,
           workdir: Path | None = None, progress: ProgressFn | None = None) -> list[SourceDoc]:
    pdfs = collect_pdfs(Path(path), workdir)
    if not pdfs:
        raise ValueError(f"No PDF files found in {path}")
    docs = []
    for idx, pdf_path in enumerate(pdfs, start=1):
        if progress:
            progress(f"Reading {pdf_path.name}")
        docs.append(ingest_pdf(pdf_path, f"d{idx:02d}", ocr_threshold=ocr_threshold, min_chars=min_chars, progress=progress))
    return docs


def summarise(docs: Iterable[SourceDoc]) -> PackageSummary:
    docs = list(docs)
    pages = [p for d in docs for p in d.pages]
    return PackageSummary(
        n_docs=len(docs), n_pages=len(pages),
        n_scanned=sum(p.is_scanned for p in pages), n_low_ocr=sum(p.low_ocr for p in pages),
        files=[d.source_file for d in docs],
    )
