"""Pipeline orchestration. Each stage is a function; run() strings them together and
reports progress through a callback so the CLI and the web status page share one path."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import classify as classify_mod
from . import extract as extract_mod
from . import ingest as ingest_mod
from .llm import LLM, make_llm, usage_cost
from .schemas import DiscardedFact, Fact, PageClassification, Section, SourceDoc, Usage
from .settings import Settings, load_settings

log = logging.getLogger(__name__)

STAGES = [
    ("ingest", "Reading files and running OCR on scans"),
    ("classify", "Classifying documents by type"),
    ("extract", "Extracting minutes, resolutions and dates"),
    ("thread", "Matching against the flag taxonomy"),
    ("anchor", "Anchoring every claim to a source page"),
    ("report", "Assembling the report"),
]

ProgressFn = Callable[[str, str, str], None]   # (stage_key, status: run|done, detail)


@dataclass
class RunState:
    settings: Settings
    llm: LLM
    docs: list[SourceDoc] = field(default_factory=list)
    classifications: list[PageClassification] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    discarded: list[DiscardedFact] = field(default_factory=list)
    building: dict[str, Any] = field(default_factory=dict)
    usage: list[Usage] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def _noop(stage: str, status: str, detail: str) -> None:
    pass


def stage_ingest(state: RunState, input_path: Path, workdir: Path | None, progress: ProgressFn) -> None:
    th = state.settings.thresholds
    progress("ingest", "run", "")
    state.docs = ingest_mod.ingest(
        input_path, ocr_threshold=th.get("ocr_confidence", 70), min_chars=th.get("ocr_min_chars_per_page", 40),
        workdir=workdir, progress=lambda d: progress("ingest", "run", d),
    )
    s = ingest_mod.summarise(state.docs)
    progress("ingest", "done", f"{s.n_docs} files, {s.n_pages} pages, {s.n_scanned} OCR'd, {s.n_low_ocr} low confidence")


def stage_classify(state: RunState, progress: ProgressFn) -> None:
    th = state.settings.thresholds
    progress("classify", "run", "")
    sections, cls, usage = classify_mod.classify(
        state.docs, state.llm, state.settings.models["classify"],
        threshold=th.get("classification_confidence", 0.7), batch_size=th.get("pages_per_classify_call", 8),
        progress=lambda d: progress("classify", "run", d),
    )
    state.sections, state.classifications = sections, cls
    state.usage.extend(usage)
    low = sum(s.low_confidence for s in sections)
    progress("classify", "done", f"{len(sections)} sections, {low} below confidence threshold")


def stage_extract(state: RunState, progress: ProgressFn) -> None:
    th = state.settings.thresholds
    progress("extract", "run", "")
    facts, discarded, usage = extract_mod.extract(
        state.sections, state.docs, state.llm, state.settings.models["extract"], state.settings.taxonomy,
        unit=state.settings.extra.get("unit"), pages_per_call=th.get("pages_per_extract_call", 12),
        progress=lambda d: progress("extract", "run", d),
    )
    state.facts, state.discarded = facts, discarded
    state.building = extract_mod.building_metadata(facts, state.settings.taxonomy)
    state.usage.extend(usage)
    absent = sum(f.kind == "document_absent" for f in facts)
    progress("extract", "done", f"{len(facts) - absent} facts anchored, {len(discarded)} discarded, {absent} document types absent")


def run(input_path: Path, *, settings: Settings | None = None, llm: LLM | None = None,
        stop_after: str | None = None, progress: ProgressFn = _noop, workdir: Path | None = None) -> RunState:
    settings = settings or load_settings()
    state = RunState(settings=settings, llm=llm or make_llm(settings))
    stage_ingest(state, Path(input_path), workdir, progress)
    if stop_after == "ingest":
        return state
    stage_classify(state, progress)
    if stop_after == "classify":
        return state
    stage_extract(state, progress)
    if stop_after == "extract":
        return state
    return state


def cost_summary(state: RunState) -> dict[str, Any]:
    return usage_cost(state.usage, state.settings.pricing)
