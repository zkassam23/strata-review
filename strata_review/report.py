"""Stage 5: render the agent and client reports from the prototype template, plus flags.json.

The template refuses to render a flag with an empty citation list: cites_or_refuse() is called
from inside the template for every flag and raises ReportRefused, which aborts the render.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from .extract import _fmt_date
from .llm import usage_cost
from .schemas import ReviewResult

TEMPLATES = Path(__file__).parent / "templates"
RISK_COLOUR = {"Elevated": "var(--red)", "Raised": "var(--red)", "Moderate": "var(--amb)", "Low": "var(--grn)"}


class ReportRefused(RuntimeError):
    """Raised when a flag reaches the template without citations."""


def cites_or_refuse(flag: Any, view: str) -> list:
    get = (lambda k: flag.get(k)) if isinstance(flag, dict) else (lambda k: getattr(flag, k, None))
    agent = get("citations") or []
    if not agent:
        raise ReportRefused(f"flag {get('flag_id')} ({get('category')}) has no citations; refusing to render")
    if view == "client":
        return get("client_citations") or agent
    return agent


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]), undefined=StrictUndefined)
    env.globals["cites_or_refuse"] = cites_or_refuse
    return env


def _address_line(result: ReviewResult) -> str:
    b = result.building
    addr = b.get("address") or "Address not stated in the documents"
    addr = addr.rsplit(", BC", 1)[0]
    parts = [p.strip() for p in addr.split(",")]
    street = parts[0] if parts else addr
    unit = result.unit or b.get("unit_number")
    return f"Suite {unit}, {street}" if unit else street


def _meta_line(result: ReviewResult) -> str:
    b = result.building
    bits = []
    if b.get("strata_plan"):
        bits.append(f"Strata plan {b['strata_plan']}")
    city = b.get("city")
    if not city and b.get("address") and "," in b["address"]:
        city = b["address"].rsplit(", BC", 1)[0].split(",")[-1].strip()
    if city:
        bits.append(city)
    bits.append(f"{result.package.n_pages} pages across {result.package.n_docs} documents")
    built = " ".join(x for x in (f"built {b['year_built']}" if b.get("year_built") else "", b.get("construction", "")) if x)
    if built:
        bits.append(built.replace("built ", "built ", 1).replace(" concrete", ", concrete") if b.get("year_built") and b.get("construction") else built)
    return " · ".join(bits)


_MONEY_RX = re.compile(r"\$([\d,]+)(?:\.\d+)?")


def round_client_money(text: str) -> str:
    """Client copy: every dollar figure of $1,000 or more rounded to the nearest thousand."""
    def rep(m):
        v = float(m.group(1).replace(",", ""))
        return f"${round(v / 1000) * 1000:,.0f}" if v >= 1000 else m.group(0)
    return _MONEY_RX.sub(rep, text)


def build_context(result: ReviewResult, tenant: dict[str, Any], taxonomy: dict[str, Any], view: str) -> dict[str, Any]:
    flags = result.flags
    n_red = sum(f.severity == "red" for f in flags)
    n_amber = sum(f.severity == "amber" for f in flags)
    labels = {c: spec["label"] for c, spec in taxonomy["categories"].items()}
    client_labels = {c: spec.get("client_label", spec["label"]) for c, spec in taxonomy["categories"].items()}

    summary_rows, quiet_line, absent_lines, body_flags = [], "", [], []
    exposure_total = result.exposure_total
    if view == "client":
        body_flags = []
        for f in flags:
            if f.severity not in ("red", "amber") or f.linked_to:
                continue
            cf = f.model_copy()
            cf.client_title = round_client_money(cf.client_title)
            cf.client_text = round_client_money(cf.client_text)
            cf.client_exposure = round_client_money(cf.client_exposure)
            body_flags.append(cf)
        exposure_total = round_client_money(result.exposure_total)
        shares = [f for f in body_flags if f.client_exposure.startswith("Roughly")]
        if len(shares) == 1:   # one determinable share: the card and the tile show the same figure
            exposure_total = shares[0].client_exposure.replace("Roughly ", "")
        note_cats = [client_labels[c.category] for c in result.category_status if c.state == "flagged" and c.severity == "note"]
        for c in result.category_status:
            if c.state == "flagged" and c.severity in ("red", "amber"):
                summary_rows.append({"label": client_labels[c.category], "pill": c.severity})
        if note_cats:
            quiet_line = "Also checked, nothing found: " + ", ".join(x.lower() for x in note_cats) + "."
        for c in result.category_status:
            if c.state == "absent":
                absent_lines.append(f"Not checked: {client_labels[c.category].lower()}. {c.text}")
            elif c.state == "none_found":
                absent_lines.append(f"{client_labels[c.category]}: no mention found in the documents. That is not the same as confirmed clear.")
    else:
        body_flags = list(flags)
        for c in result.category_status:
            summary_rows.append({"label": labels[c.category], "pill": c.severity if c.state == "flagged" else c.state})
        for c in result.category_status:
            if c.state in ("absent", "none_found"):
                absent_lines.append(f"{labels[c.category]}: {c.text}")

    minutes = [s for s in result.sections if s.doc_type in ("council_minutes", "agm_sgm_minutes")]
    mdates = sorted(s.meeting_date for s in minutes if s.meeting_date)
    files = sorted({s.source_file for s in minutes})
    if minutes:
        minutes_line = (f"Minutes searched: {len(minutes)} council and general meetings, {_fmt_date(mdates[0])} to {_fmt_date(mdates[-1])} "
                        f"({', '.join(files)}). Anything outside that range is not covered by this report.") if mdates else f"Minutes searched: {len(minutes)} sections in {', '.join(files)}."
    else:
        minutes_line = "No council or general meeting minutes were supplied. Nothing in this report reflects council discussion."
    by_file: dict[str, list] = {}
    for s in result.sections:
        by_file.setdefault(s.source_file, []).append(s)
    doc_lines = []
    for fn, secs in by_file.items():
        types = []
        for s in secs:
            t = s.doc_type.replace("_", " ")
            if t not in types:
                types.append(t)
        doc_lines.append(f"{fn}: {', '.join(types)} ({len(secs)} section{'s' if len(secs) != 1 else ''})")
    absent_types = [f.summary for f in result.facts if f.kind == "document_absent"]
    disagreements = [f for f in flags if f.judge_disagreed]
    cost = usage_cost(result.usage, {}) if result.usage else None
    usage_line = ""
    if cost:
        usage_line = "; ".join(f"{m}: {r['calls']} calls, {r['input_tokens']:,} in / {r['output_tokens']:,} out" for m, r in cost["per_model"].items())
        if cost["mocked"]:
            usage_line += " (offline mock, no model was called)"
    return {
        "view": view, "result": result, "tenant": tenant, "generated_on": _fmt_date(result.generated_on),
        "address_line": _address_line(result), "meta_line": _meta_line(result),
        "risk_colour": RISK_COLOUR.get(result.overall_risk, "var(--paper)"), "n_red": n_red, "n_amber": n_amber,
        "summary_rows": summary_rows, "body_flags": body_flags, "exposure_total": exposure_total,
        "questions": result.client_questions if view == "client" else result.questions, "quiet_line": quiet_line, "absent_lines": absent_lines,
        "minutes_line": minutes_line, "doc_lines": doc_lines, "absent_types": absent_types, "disagreements": disagreements,
        "usage_line": usage_line,
    }


def render(result: ReviewResult, tenant: dict[str, Any], taxonomy: dict[str, Any], view: str) -> str:
    ctx = build_context(result, tenant, taxonomy, view)
    return _env().get_template("report.html").render(**ctx)


def flags_json(result: ReviewResult) -> dict[str, Any]:
    return {
        "generated_on": result.generated_on, "building": result.building, "unit": result.unit,
        "overall_risk": result.overall_risk, "exposure_total": result.exposure_total,
        "package": result.package.model_dump(),
        "flags": [f.model_dump() for f in result.flags],
        "category_status": [c.model_dump() for c in result.category_status],
        "threads": [t.model_dump() for t in result.threads],
        "questions": result.questions,
        "low_ocr_pages": [{"source_file": p.source_file, "page_number": p.page_number, "ocr_confidence": p.ocr_confidence} for p in result.low_ocr_pages],
        "low_confidence_sections": [s.model_dump() for s in result.low_confidence_sections],
        "discarded": [d.model_dump() for d in result.discarded],
        "usage": [u.model_dump() for u in result.usage],
    }


def write_outputs(result: ReviewResult, tenant: dict[str, Any], taxonomy: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"agent": out_dir / "agent.html", "client": out_dir / "client.html", "flags": out_dir / "flags.json"}
    paths["agent"].write_text(render(result, tenant, taxonomy, "agent"), "utf-8")
    paths["client"].write_text(render(result, tenant, taxonomy, "client"), "utf-8")
    paths["flags"].write_text(json.dumps(flags_json(result), indent=1, default=str), "utf-8")
    return paths
