"""Stage 2: classify pages by document type and assemble typed sections.

Sections are built by content, never by filename. Within a file, consecutive pages of the
same type form a section; minutes additionally split on meeting date so each meeting is
extracted on its own. A single low-confidence page sandwiched between two pages of another
type is relabelled to match its neighbours and the original label is kept on the record
(smoothed_from). Sections whose mean confidence is under the threshold are marked
low_confidence and surfaced in the report appendix rather than silently accepted.
"""
from __future__ import annotations

import logging
from typing import Callable

from . import prompts
from .llm import LLM
from .schemas import Page, PageClassification, Section, SourceDoc, Usage

log = logging.getLogger(__name__)


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def classify_pages(docs: list[SourceDoc], llm: LLM, model: str, *, batch_size: int = 8,
                   progress: Callable[[str], None] | None = None) -> tuple[list[PageClassification], list[Usage]]:
    results: list[PageClassification] = []
    usages: list[Usage] = []
    for doc in docs:
        for batch in _chunks(doc.pages, batch_size):
            if progress:
                progress(f"Classifying {doc.source_file} p.{batch[0].page_number}-{batch[-1].page_number}")
            user = prompts.render_pages(batch, prompts.PAGE_CHARS_FOR_CLASSIFY)
            payload, usage = llm.complete_json(
                task="classify_pages", model=model, system=prompts.CLASSIFY_SYSTEM, user=user,
                schema=prompts.CLASSIFY_SCHEMA, max_tokens=4000,
            )
            usages.append(usage)
            by_page = {int(r["page_number"]): r for r in payload.get("pages", [])}
            for p in batch:
                r = by_page.get(p.page_number)
                if r is None:
                    log.warning("classifier returned nothing for %s p.%s; recording as other/0.0", doc.source_file, p.page_number)
                    results.append(PageClassification(doc_id=doc.doc_id, page_number=p.page_number, doc_type="other", confidence=0.0))
                    continue
                dt = r.get("doc_type") if r.get("doc_type") in prompts.DOC_TYPE_LIST else "other"
                results.append(PageClassification(
                    doc_id=doc.doc_id, page_number=p.page_number, doc_type=dt,
                    confidence=max(0.0, min(1.0, float(r.get("confidence", 0.0)))),
                    meeting_date=r.get("meeting_date") or None, title=r.get("title") or None,
                ))
    return results, usages


def smooth(cls: list[PageClassification], threshold: float) -> list[PageClassification]:
    """Relabel a lone low-confidence page whose neighbours agree with each other."""
    by_doc: dict[str, list[PageClassification]] = {}
    for c in cls:
        by_doc.setdefault(c.doc_id, []).append(c)
    for seq in by_doc.values():
        seq.sort(key=lambda c: c.page_number)
        for i in range(1, len(seq) - 1):
            prev, cur, nxt = seq[i - 1], seq[i], seq[i + 1]
            if cur.doc_type != prev.doc_type and prev.doc_type == nxt.doc_type and cur.confidence < threshold:
                log.info("smoothing %s p.%s %s->%s (conf %.2f)", cur.doc_id, cur.page_number, cur.doc_type, prev.doc_type, cur.confidence)
                cur.smoothed_from = cur.doc_type
                cur.doc_type = prev.doc_type
                if cur.meeting_date is None and prev.meeting_date == nxt.meeting_date:
                    cur.meeting_date = prev.meeting_date
    return cls


MINUTES_TYPES = {"council_minutes", "agm_sgm_minutes"}


def build_sections(docs: list[SourceDoc], cls: list[PageClassification], threshold: float) -> list[Section]:
    """Group consecutive pages of one type (and one meeting date for minutes) into sections."""
    by_key = {(c.doc_id, c.page_number): c for c in cls}
    sections: list[Section] = []
    n = 0
    for doc in docs:
        run: list[tuple[Page, PageClassification]] = []

        def flush():
            nonlocal n
            if not run:
                return
            n += 1
            pages_, cls_ = zip(*run)
            conf = sum(c.confidence for c in cls_) / len(cls_)
            dates = [c.meeting_date for c in cls_ if c.meeting_date]
            titles = [c.title for c in cls_ if c.title]
            sections.append(Section(
                section_id=f"s{n:03d}", doc_id=doc.doc_id, source_file=doc.source_file,
                doc_type=cls_[0].doc_type, page_start=pages_[0].page_number, page_end=pages_[-1].page_number,
                confidence=round(conf, 3), low_confidence=conf < threshold,
                meeting_date=max(set(dates), key=dates.count) if dates else None,
                title=titles[0] if titles else None,
            ))
            run.clear()

        for p in doc.pages:
            c = by_key[(p.doc_id, p.page_number)]
            if run:
                last = run[-1][1]
                same_type = last.doc_type == c.doc_type
                # minutes: a page dated differently from the run's meeting starts a new section;
                # undated continuation pages (including blank scans) stay with the run
                run_date = next((rc.meeting_date for _, rc in run if rc.meeting_date), None)
                new_meeting = (c.doc_type in MINUTES_TYPES and c.meeting_date and run_date
                               and c.meeting_date != run_date)
                if not same_type or new_meeting:
                    flush()
            run.append((p, c))
            if c.doc_type in MINUTES_TYPES and c.meeting_date and run and run[-1][1] is c:
                # propagate the meeting date backwards onto undated pages in the same run
                for _, prior in run[:-1]:
                    if prior.meeting_date is None:
                        prior.meeting_date = c.meeting_date
        flush()
    return sections


def classify(docs: list[SourceDoc], llm: LLM, model: str, *, threshold: float, batch_size: int = 8,
             progress: Callable[[str], None] | None = None) -> tuple[list[Section], list[PageClassification], list[Usage]]:
    cls, usages = classify_pages(docs, llm, model, batch_size=batch_size, progress=progress)
    cls = smooth(cls, threshold)
    sections = build_sections(docs, cls, threshold)
    return sections, cls, usages
