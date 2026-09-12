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

    state = pipeline.run(Path(args.input), settings=settings, stop_after=args.stop_after, progress=_progress)
    if args.stop_after in ("ingest", "classify") or not hasattr(state, "result"):
        print_sections(state)
    print_cost(state)
    if args.dump:
        payload = {"docs": [d.model_dump(exclude={"pages"}) | {"pages": [p.model_dump(exclude={"text"}) for p in d.pages]} for d in state.docs],
                   "sections": [s.model_dump() for s in state.sections],
                   "classifications": [c.model_dump() for c in state.classifications]}
        Path(args.dump).write_text(json.dumps(payload, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
