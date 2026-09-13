"""Pipeline orchestration. Each stage is a function; run() strings them together and
reports progress through a callback so the CLI and the web status page share one path."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import classify as classify_mod
from . import extract as extract_mod
from . import judge as judge_mod
from . import report as report_mod
from . import threads as threads_mod
from . import ingest as ingest_mod
from .llm import LLM, make_llm, usage_cost
from .schemas import CategoryStatus, DiscardedFact, Fact, Flag, PageClassification, ReviewResult, Section, SourceDoc, Thread, Usage
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
    threads: list[Thread] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    category_status: list[CategoryStatus] = field(default_factory=list)
    anchor_errors: list[str] = field(default_factory=list)
    result: ReviewResult | None = None
    outputs: dict[str, Path] = field(default_factory=dict)
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


def stage_thread(state: RunState, progress: ProgressFn) -> None:
    progress("thread", "run", "")
    tax = state.settings.taxonomy
    state.threads = threads_mod.build_threads(state.facts, state.sections, state.building, tax)
    progress("thread", "run", f"{len(state.threads)} threads assembled; judging severity")
    flags, questions, usage = judge_mod.judge_threads(state.threads, state.facts, state.sections, state.building, tax,
                                                      state.llm, state.settings.models["judge"])
    state.flags, state.questions = flags, questions
    state.usage.extend(usage)
    state.category_status = judge_mod.category_status(flags, state.sections, len(state.docs), tax)
    reds = sum(f.severity == "red" for f in flags)
    ambers = sum(f.severity == "amber" for f in flags)
    dis = sum(f.judge_disagreed for f in flags)
    progress("thread", "done", f"{len(state.threads)} threads, {len(flags)} flags ({reds} red, {ambers} amber), {dis} judge disagreement(s)")


def stage_anchor(state: RunState, progress: ProgressFn) -> None:
    """Every flag must carry citations, and every page citation must resolve to a real page."""
    progress("anchor", "run", "")
    pages = {(d.source_file, p.page_number) for d in state.docs for p in d.pages}
    errors = []
    kept = []
    for fl in state.flags:
        if not fl.citations:
            errors.append(f"{fl.flag_id} {fl.category}: no citations; flag removed")
            continue
        bad = [c for c in fl.citations if c.kind == "page" and (c.source_doc, c.page_number) not in pages]
        if bad:
            errors.append(f"{fl.flag_id} {fl.category}: citation(s) do not resolve: {[c.label for c in bad]}; flag removed")
            continue
        kept.append(fl)
    state.flags = kept
    state.anchor_errors = errors
    for e in errors:
        log.error("anchor check: %s", e)
    n_cites = sum(len(f.citations) for f in kept)
    progress("anchor", "done", f"{len(kept)} flags, {n_cites} citations verified, {len(errors)} removed")


def assemble_result(state: RunState) -> ReviewResult:
    tax = state.settings.taxonomy
    return ReviewResult(
        building=state.building, unit=state.settings.extra.get("unit"), package=ingest_mod.summarise(state.docs),
        sections=state.sections, facts=state.facts, discarded=state.discarded, threads=state.threads, flags=state.flags,
        category_status=state.category_status, questions=state.questions,
        client_questions=judge_mod.client_questions(state.flags, state.settings.extra.get("unit")),
        low_ocr_pages=[p.model_copy(update={"text": ""}) for d in state.docs for p in d.pages if p.low_ocr],
        low_confidence_sections=[s for s in state.sections if s.low_confidence], usage=state.usage,
        overall_risk=judge_mod.overall_risk(state.flags), exposure_total=judge_mod.exposure_total(state.flags, tax),
    )


def stage_report(state: RunState, out_dir: Path | None, progress: ProgressFn) -> None:
    progress("report", "run", "")
    state.result = assemble_result(state)
    if out_dir:
        state.outputs = report_mod.write_outputs(state.result, state.settings.tenant, state.settings.taxonomy, out_dir)
        progress("report", "done", f"wrote {', '.join(p.name for p in state.outputs.values())} to {out_dir}")
    else:
        progress("report", "done", "result assembled (no output folder)")


def run(input_path: Path, *, settings: Settings | None = None, llm: LLM | None = None,
        stop_after: str | None = None, progress: ProgressFn = _noop, workdir: Path | None = None,
        out_dir: Path | None = None) -> RunState:
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
    stage_thread(state, progress)
    if stop_after == "thread":
        return state
    stage_anchor(state, progress)
    if stop_after == "anchor":
        return state
    stage_report(state, out_dir, progress)
    return state


def cost_summary(state: RunState) -> dict[str, Any]:
    return usage_cost(state.usage, state.settings.pricing)
