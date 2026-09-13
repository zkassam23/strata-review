"""Thin CLI around the core module.

  strata-review ./package --agent "Kassam & Associates" --brokerage "eXp Realty Canada" --unit 1204 --out ./report
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import pipeline
from .settings import load_settings


def _progress(stage: str, status: str, detail: str) -> None:
    label = dict(pipeline.STAGES)[stage]
    if status == "done":
        print(f"  [done] {label}: {detail}", file=sys.stderr)
    elif detail:
        print(f"         {detail}", file=sys.stderr, end="\r")


def print_sections(state: pipeline.RunState) -> None:
    print("\nSections (by content, not filename):")
    print(f"  {'id':6} {'type':22} {'pages':9} {'conf':5} {'date':11} file")
    for s in state.sections:
        flag = " LOW" if s.low_confidence else ""
        print(f"  {s.section_id:6} {s.doc_type:22} {s.page_start:>3}-{s.page_end:<4} {s.confidence:.2f}{flag:4} {s.meeting_date or '':11} {s.source_file}")
    low_ocr = [p for d in state.docs for p in d.pages if p.low_ocr]
    scanned = sum(p.is_scanned for d in state.docs for p in d.pages)
    print(f"\nOCR: {scanned} scanned pages; {len(low_ocr)} below confidence threshold:")
    for p in low_ocr:
        print(f"  {p.source_file} p.{p.page_number} conf={p.ocr_confidence}")
    sm = [c for c in state.classifications if c.smoothed_from]
    if sm:
        print(f"\nSmoothed pages (lone low-confidence page relabelled to neighbours): {len(sm)}")
        for c in sm:
            print(f"  {c.doc_id} p.{c.page_number}: {c.smoothed_from} -> {c.doc_type}")


def print_facts(state: pipeline.RunState) -> None:
    print(f"\nFacts ({len(state.facts)}), grouped by section:")
    by_sec = {}
    for f in state.facts:
        by_sec.setdefault(f.section_id, []).append(f)
    secs = {s.section_id: s for s in state.sections}
    for sid, facts in by_sec.items():
        s = secs.get(sid)
        head = f"{s.doc_type} {s.source_file} p.{s.page_start}-{s.page_end}" + (f" ({s.meeting_date})" if s and s.meeting_date else "") if s else "(generated: absences)"
        print(f"  -- {head}")
        for f in facts:
            amt = ""
            if f.amount_min is not None:
                amt = f" ${f.amount_min:,.0f}" + (f"-${f.amount_max:,.0f}" if f.amount_max not in (None, f.amount_min) else "")
            pages = ",".join(str(c.page_number) for c in f.citations if c.page_number)
            print(f"     {f.fact_id} {f.category}/{f.kind:26} {(f.status or ''):16} {(f.topic or '')[:34]:34}{amt:22} p.{pages or '-'}  {f.summary[:70]}")
    print(f"\nDiscarded by the anchor rule ({len(state.discarded)}):")
    for d in state.discarded:
        print(f"  {d.section_id}: {d.reason}  <- {str(d.payload.get('summary', ''))[:70]!r}")
    if state.building:
        print("\nBuilding metadata:", {k: v for k, v in state.building.items() if not k.endswith('_cite')})


def print_threads(state: pipeline.RunState) -> None:
    facts = {f.fact_id: f for f in state.facts}
    print(f"\nThreads ({len(state.threads)}):")
    for t in state.threads:
        m = t.metrics
        tag = " STALE" if m.get("stale_single_mention") else ""
        dis = f"  judge={t.judge_severity} DISAGREES" if t.judge_severity and t.judge_severity != t.rule_severity else ""
        print(f"  {t.thread_id} {t.category:28} {t.topic[:36]:36} rule={t.rule_severity:5}{tag}{dis}  mentions={m.get('n_mentions')} {t.first_date or ''}..{t.last_date or ''} latest={m.get('latest_status')}")
        for fid in t.fact_ids[:8]:
            f = facts[fid]
            amt = f" {f.amount_min:,.0f}-{f.amount_max:,.0f}" if f.amount_max is not None and f.amount_min != f.amount_max else (f" {f.amount_max:,.0f}" if f.amount_max is not None else "")
            print(f"       {f.date or '          '} {f.doc_type[:12]:12} {(f.status or ''):16}{amt:22} {f.citations[0].label}")
        if len(t.fact_ids) > 8:
            print(f"       ... {len(t.fact_ids) - 8} more")
        keys = [k for k in ("reserve_ratio", "crf_balance", "crf_recommended", "water_deductible", "report_age_years", "completion",
                            "max_building_cost", "arrears_amount", "parking_designation", "storage_designation", "has_rental_restriction") if k in m]
        print("       metrics: " + ", ".join(f"{k}={m[k]}" for k in keys) + f"  | exposure: {m.get('exposure')}" + (f"  [{m['exposure_calc']}]" if m.get("exposure_calc") else ""))
        print(f"       rule: {t.rule_criteria}")
    print(f"\nFlags ({len(state.flags)}):")
    for f in state.flags:
        print(f"  [{f.severity.upper():5}] {f.category:28} {f.title}   exposure: {f.exposure}")
        if f.judge_disagreed:
            print(f"          JUDGE DISAGREED: rule={f.rule_severity} judge={f.judge_severity}: {f.judge_rationale[:150]}")
        for c in f.citations:
            print(f"          - {c.label}")
    print("\nCategory status:")
    for c in state.category_status:
        print(f"  {c.label:32} {c.state:10} {c.severity or ''}  {c.text or ''}")
    print("\nQuestions for the listing agent:")
    for q in state.questions:
        print(f"  - {q}")
    if state.anchor_errors:
        print("\nAnchor check removed flags:")
        for e in state.anchor_errors:
            print("  " + e)


def print_cost(state: pipeline.RunState) -> None:
    c = pipeline.cost_summary(state)
    tag = " (mock, estimated)" if c["mocked"] else ""
    print(f"\nLLM usage{tag}:")
    for model, row in c["per_model"].items():
        print(f"  {model:20} {row['calls']:4} calls  in {row['input_tokens']:>8,}  out {row['output_tokens']:>7,}  ${row['usd']:.3f}")
    print(f"  total ${c['total_usd']:.3f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="strata-review", description="Strata document review for BC condo transactions")
    ap.add_argument("input", help="folder of PDFs, a single PDF, or a zip")
    ap.add_argument("--agent", help="agent or team name (defaults to config/tenant.yaml)")
    ap.add_argument("--brokerage", help="brokerage (defaults to config/tenant.yaml)")
    ap.add_argument("--unit", help="unit or suite number under review")
    ap.add_argument("--out", default="./report", help="output folder for agent.html, client.html, flags.json")
    ap.add_argument("--mock", action="store_true", help="use the offline heuristic LLM (no API key needed)")
    ap.add_argument("--stop-after", choices=[k for k, _ in pipeline.STAGES], help="stop after a stage and print its output")
    ap.add_argument("--dump", help="write intermediate state as JSON to this path")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = load_settings(llm_mode="mock" if args.mock else None)
    if settings.llm_mode == "mock" and not args.mock:
        print("ANTHROPIC_API_KEY not set: running with the offline mock LLM. Put the key in .env for a real run.", file=sys.stderr)
    if args.agent:
        settings.tenant["agent_name"] = args.agent
    if args.brokerage:
        settings.tenant["brokerage"] = args.brokerage
    settings.extra["unit"] = args.unit

    state = pipeline.run(Path(args.input), settings=settings, stop_after=args.stop_after, progress=_progress,
                         out_dir=None if args.stop_after else Path(args.out))
    if args.stop_after in ("ingest", "classify"):
        print_sections(state)
    if state.facts and args.stop_after == "extract":
        print_facts(state)
    if state.threads and args.stop_after in ("thread", "anchor"):
        print_threads(state)
    if state.result:
        r = state.result
        print(f"\nOverall risk: {r.overall_risk}   flags: {sum(f.severity=='red' for f in r.flags)} red, {sum(f.severity=='amber' for f in r.flags)} amber, {sum(f.severity=='note' for f in r.flags)} note   exposure: {r.exposure_total}")
        for f in r.flags:
            print(f"  [{f.severity.upper():5}] {f.title}" + ("   (judge disagreed)" if f.judge_disagreed else ""))
        print(f"\nDIAGNOSTICS (also in the agent copy appendix)")
        print(f"Discarded by the anchor rule ({len(r.discarded)}):")
        for d in r.discarded:
            print(f"  {d.section_id}: {d.reason}  <- {str(d.payload.get('summary', ''))[:80]!r}")
        print(f"Low-confidence sections ({len(r.low_confidence_sections)}):")
        for s in r.low_confidence_sections:
            print(f"  {s.source_file} pp.{s.page_start}-{s.page_end}: {s.doc_type} at {s.confidence:.2f}" + (f" ({s.meeting_date})" if s.meeting_date else ""))
        print(f"Low-OCR pages ({len(r.low_ocr_pages)}):")
        for p in r.low_ocr_pages:
            print(f"  {p.source_file} p.{p.page_number}: confidence {p.ocr_confidence}")
        dis = [f for f in r.flags if f.judge_disagreed]
        print(f"Model cross-check disagreements ({len(dis)}):")
        for f in dis:
            print(f"  {f.category}: rule={f.rule_severity} judge={f.judge_severity}: {f.judge_rationale}")
        tpl = [f for f in r.flags if f.narrative_source == 'template']
        if tpl and not any(u.mocked for u in r.usage):
            print(f"Narratives replaced by template after failing the figure check ({len(tpl)}): " + ", ".join(f.category for f in tpl))
        for k, pth in state.outputs.items():
            print(f"  wrote {pth}")
    print_cost(state)
    if args.dump:
        payload = {"docs": [d.model_dump(exclude={"pages"}) | {"pages": [p.model_dump(exclude={"text"}) for p in d.pages]} for d in state.docs],
                   "sections": [s.model_dump() for s in state.sections],
                   "classifications": [c.model_dump() for c in state.classifications],
                   "facts": [f.model_dump() for f in state.facts], "discarded": [d.model_dump() for d in state.discarded],
                   "building": state.building, "threads": [t.model_dump() for t in state.threads],
                   "flags": [f.model_dump() for f in state.flags], "questions": state.questions,
                   "category_status": [c.model_dump() for c in state.category_status]}
        Path(args.dump).write_text(json.dumps(payload, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
