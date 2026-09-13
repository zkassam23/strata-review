"""Stage 4: cross-reference facts into issue threads, compute metrics, apply the config rules.

Threading
  per_topic categories: facts link when their topic tokens overlap (overlap coefficient over
  stemmed, stop-word-free tokens), transitively via union-find, so "roof membrane replacement"
  on 8 Nov 2025 and 8 Apr 2026 are one thread with an ordered timeline.
  single categories: every fact in the category forms one building-level thread.

Metrics are computed in code from the thread's facts (see metrics_for). Rules in the taxonomy
are evaluated in order against those metrics; the first match is the rule severity. The
staleness rule may cap it. Exposure is computed from unit entitlement and the levy range and
reads "Not determinable from these documents" when either is missing.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from .schemas import Citation, Fact, Section, Thread

log = logging.getLogger(__name__)

STOP = {"the", "a", "an", "of", "and", "or", "to", "for", "in", "on", "at", "by", "with", "unit", "units", "bylaw",
        "strata", "building", "issue", "matter", "item", "north", "south", "east", "west", "elevation"}
SEVERITY_RANK = {"red": 3, "amber": 2, "note": 1}
CONTEXT_KINDS = {"levy_none_stated"}   # building-level statements attached to every thread in their category
WORK_KINDS = {"major_work_discussed", "quote_obtained", "work_deferred", "levy_proposed", "levy_passed"}


def _stem(w: str) -> str:
    for suf in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def topic_tokens(topic: str | None) -> set[str]:
    words = re.findall(r"[a-z]+", (topic or "").lower())
    return {_stem(w) for w in words if w not in STOP and len(w) > 2}


def similar(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class _UF:
    def __init__(self, n): self.p = list(range(n))
    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i
    def union(self, i, j): self.p[self.find(i)] = self.find(j)


def group_threads(facts: list[Fact], taxonomy: dict[str, Any], link_threshold: float = 0.5) -> list[Thread]:
    cats = taxonomy["categories"]
    threads: list[Thread] = []
    n = 0
    for cat, spec in cats.items():
        cf = [f for f in facts if f.category == cat]
        if not cf:
            continue
        groups: list[list[Fact]]
        if spec.get("threading", "single") == "single":
            groups = [cf]
        else:
            toks = [topic_tokens(f.topic) for f in cf]
            uf = _UF(len(cf))
            for i in range(len(cf)):
                for j in range(i + 1, len(cf)):
                    same_report = (cf[i].section_id == cf[j].section_id and cf[i].doc_type not in ("council_minutes", "agm_sgm_minutes"))
                    if same_report or similar(toks[i], toks[j]) >= link_threshold:
                        uf.union(i, j)
            by_root: dict[int, list[Fact]] = {}
            context = [f for f in cf if f.kind in CONTEXT_KINDS]
            for i, f in enumerate(cf):
                if f.kind in CONTEXT_KINDS:
                    continue
                by_root.setdefault(uf.find(i), []).append(f)
            groups = [g + context for g in by_root.values()] or ([context] if context else [])
        for g in groups:
            g.sort(key=lambda f: (f.date or "0000-00-00", f.fact_id))
            n += 1
            dated = [f.date for f in g if f.date]
            # topic label: most common topic string in the group
            topics = [f.topic for f in g if f.topic and f.kind not in CONTEXT_KINDS] or [f.topic for f in g if f.topic]
            topic = max(set(topics), key=topics.count) if topics else spec["label"].lower()
            threads.append(Thread(thread_id=f"t{n:03d}", category=cat, topic=topic, fact_ids=[f.fact_id for f in g],
                                  first_date=min(dated) if dated else None, last_date=max(dated) if dated else None))
    return threads


# ---- metrics --------------------------------------------------------------------------

def _latest(facts: list[Fact], kind: str | None = None, prefer: tuple[str, ...] = ()) -> Fact | None:
    pool = [f for f in facts if (kind is None or f.kind == kind)]
    if not pool:
        return None
    return max(pool, key=lambda f: (f.date or "", prefer.index(f.doc_type) if f.doc_type in prefer else -1))


def _amount_facts(facts: list[Fact]) -> list[Fact]:
    return [f for f in facts if f.amount_max is not None]


def metrics_for(thread: Thread, facts: list[Fact], review_date: date, latest_doc_date: date | None,
                building: dict[str, Any], sections: list[Section]) -> dict[str, Any]:
    fs = [f for f in facts if f.fact_id in set(thread.fact_ids)]
    cat = thread.category
    m: dict[str, Any] = {"n_mentions": len(fs), "first_date": thread.first_date, "last_date": thread.last_date}
    statused = [f for f in fs if f.status and f.status != "none"]
    latest_status_fact = max(statused, key=lambda f: (f.date or "", f.fact_id)) if statused else None
    m["latest_status"] = latest_status_fact.status if latest_status_fact else (fs[-1].status if fs else None)
    if thread.last_date and latest_doc_date:
        m["months_since_last_mention"] = round((latest_doc_date - date.fromisoformat(thread.last_date)).days / 30.4, 1)
    else:
        m["months_since_last_mention"] = None
    evidence: list[str] = []

    if cat == "special_levies":
        work = [f for f in fs if f.kind in WORK_KINDS and f.amount_max is not None]
        latest_amt = max(work, key=lambda f: (f.date or "", f.fact_id)) if work else None
        m["min_building_cost"] = latest_amt.amount_min if latest_amt else None
        m["max_building_cost"] = latest_amt.amount_max if latest_amt else None
        m["levy_range_source"] = latest_amt.citations[0].label if latest_amt else None
        evidence = [f.fact_id for f in fs if f.kind in WORK_KINDS or f.kind == "levy_none_stated"]
    elif cat == "contingency_reserve":
        bal = _latest(fs, "crf_balance", prefer=("council_minutes", "agm_sgm_minutes", "form_b", "financial_statements"))
        rec = _latest(fs, "crf_recommended_balance", prefer=("financial_statements", "depreciation_report"))
        con = _latest(fs, "crf_annual_contribution", prefer=("agm_sgm_minutes", "depreciation_report", "financial_statements"))
        m["crf_balance"] = bal.amount_max if bal else None
        m["crf_balance_date"] = bal.date if bal else None
        m["crf_recommended"] = rec.amount_max if rec else None
        m["crf_target_year"] = rec.data.get("target_year") if rec else None
        m["crf_contribution"] = con.amount_max if con else None
        m["reserve_ratio"] = round(m["crf_balance"] / m["crf_recommended"], 3) if bal and rec and m["crf_recommended"] else None
        evidence = [f.fact_id for f in (bal, rec, con) if f]
    elif cat == "building_envelope":
        recs = [f for f in fs if f.kind == "envelope_recommendation" and f.status == "recommended"]
        done = [f for f in fs if f.kind == "envelope_work_completed" or f.status == "completed"]
        tend = [f for f in fs if f.kind == "envelope_work_tendered" or f.status == "tendered"]
        m["has_remediation_recommendation"] = bool(recs)
        m["has_completion_record"] = bool(done)
        m["has_tender_record"] = bool(tend)
        m["completion"] = "recorded" if done else ("tendered" if tend else "not determinable from these documents")
        m["water_ingress_mentions"] = sum(f.kind == "water_ingress" for f in fs)
        r = min(recs, key=lambda f: (f.doc_type != "engineering_report", f.date or "")) if recs else None
        m["recommendation_date"] = r.date if r else None
        m["recommendation_deadline_year"] = (r.data.get("deadline_year") or None) if r else None
        m["min_building_cost"] = r.amount_min if r else None
        m["max_building_cost"] = r.amount_max if r else None
        minutes = [s for s in sections if s.doc_type in ("council_minutes", "agm_sgm_minutes")]
        dates = sorted(s.meeting_date for s in minutes if s.meeting_date)
        m["minutes_searched"] = {"n": len(minutes), "from": dates[0] if dates else None, "to": dates[-1] if dates else None,
                                 "files": sorted({s.source_file for s in minutes})}
        evidence = [f.fact_id for f in fs if f.kind in ("envelope_recommendation", "envelope_finding", "envelope_work_completed", "envelope_work_tendered")] \
            or [f.fact_id for f in fs]
    elif cat == "depreciation_report_currency":
        rd = None
        for f in fs:
            cand = f.data.get("report_date") or (f.date if f.doc_type == "depreciation_report" else None)
            if cand and len(cand) >= 7:
                cand = cand if len(cand) == 10 else cand + "-01"
                rd = max(rd, cand) if rd else cand
        m["report_date"] = rd
        m["report_age_years"] = round((review_date - date.fromisoformat(rd)).days / 365.25, 1) if rd else None
        evidence = [f.fact_id for f in fs if f.doc_type == "depreciation_report"] or [f.fact_id for f in fs]
    elif cat == "insurance_deductibles":
        water = [f for f in fs if "water" in (f.data.get("peril", "") + (f.topic or "")).lower() and f.amount_max is not None]
        w = max(water, key=lambda f: (f.date or "", f.doc_type == "insurance_summary")) if water else None
        m["water_deductible"] = w.amount_max if w else None
        m["water_deductible_date"] = w.date if w else None
        m["has_chargeback_bylaw"] = any(f.kind == "deductible_chargeback_bylaw" for f in fs)
        chg = [f for f in fs if f.kind == "deductible_change" and f.amount_min is not None and f.amount_min != f.amount_max]
        m["deductible_change"] = (chg[-1].amount_min, chg[-1].amount_max) if chg else None
        evidence = [f.fact_id for f in ([w] if w else []) + [f for f in fs if f.kind in ("deductible_chargeback_bylaw", "deductible_change")]]
    elif cat == "litigation":
        m["has_active_litigation"] = any(f.kind in ("litigation_active", "crt_dispute") and f.status != "none" for f in fs)
        m["has_threatened_litigation"] = any(f.kind == "litigation_threatened" and f.status != "none" for f in fs)
        m["none_stated"] = any(f.kind == "litigation_none_stated" for f in fs)
        evidence = [f.fact_id for f in fs]
    elif cat == "bylaws":
        m["has_rental_restriction"] = any(f.kind == "rental_restriction" and f.status != "none" and f.doc_type in ("bylaws", "council_minutes", "agm_sgm_minutes") for f in fs)
        m["has_age_restriction"] = any(f.kind == "age_restriction" and f.status != "none" for f in fs)
        m["has_pet_restriction"] = any(f.kind == "pet_restriction" and f.status != "none" for f in fs)
        m["has_str_restriction"] = any(f.kind == "short_term_rental_restriction" and f.status != "none" for f in fs)
        rc = [f for f in fs if f.kind == "rentals_count"]
        m["rentals_count"] = rc[-1].summary if rc else None
        evidence = [f.fact_id for f in fs if f.doc_type == "bylaws"] or [f.fact_id for f in fs]
    elif cat == "parking_storage":
        pk = [f for f in fs if f.kind == "parking_designation" and f.doc_type == "form_b"]
        st = [f for f in fs if f.kind == "storage_designation" and f.doc_type == "form_b"]
        m["parking_designation"] = pk[-1].data.get("designation") if pk else None
        m["storage_designation"] = st[-1].data.get("designation") if st else None
        evidence = [f.fact_id for f in pk + st] or [f.fact_id for f in fs]
    elif cat == "form_b_unit":
        ar = [f for f in fs if f.kind in ("arrears", "levy_owing_on_unit") and f.amount_max is not None]
        m["arrears_amount"] = sum(f.amount_max for f in ar) if ar else None
        fb = [f for f in fs if f.kind == "form_b_date" and f.date]
        m["form_b_date"] = fb[-1].date if fb else None
        m["form_b_age_days"] = (review_date - date.fromisoformat(fb[-1].date)).days if fb else None
        fee = [f for f in fs if f.kind == "monthly_fees" and f.amount_max is not None]
        m["monthly_fees"] = fee[-1].amount_max if fee else None
        evidence = [f.fact_id for f in fs]
    m["evidence_fact_ids"] = evidence or [f.fact_id for f in fs]
    return m


# ---- rules ----------------------------------------------------------------------------

def _cond(c: dict[str, Any], metrics: dict[str, Any]) -> bool:
    if "always" in c:
        return bool(c["always"])
    if "any_of" in c:
        return any(_cond(x, metrics) for x in c["any_of"])
    if "all_of" in c:
        return all(_cond(x, metrics) for x in c["all_of"])
    v = metrics.get(c["metric"])
    if "is_null" in c:
        return (v is None) == bool(c["is_null"])
    if "eq" in c:
        return v == c["eq"]
    if "in" in c:
        return v in c["in"]
    if v is None:
        return False
    for op, fn in (("gte", lambda a, b: a >= b), ("gt", lambda a, b: a > b), ("lte", lambda a, b: a <= b), ("lt", lambda a, b: a < b)):
        if op in c:
            try:
                return fn(v, c[op])
            except TypeError:
                return False
    return False


def rule_severity(spec: dict[str, Any], metrics: dict[str, Any]) -> tuple[str, str]:
    for rule in spec["severity"]:
        if _cond(rule["when"], metrics):
            return rule["level"], rule["criteria"]
    return "note", "No rule matched."


def apply_staleness(thread: Thread, metrics: dict[str, Any], severity: str, taxonomy: dict[str, Any], facts_by_id: dict[str, Fact]) -> tuple[str, bool]:
    st = taxonomy.get("staleness")
    if not st:
        return severity, False
    fs = [facts_by_id[i] for i in thread.fact_ids]
    if metrics["n_mentions"] != 1 or not all(f.doc_type in ("council_minutes", "agm_sgm_minutes") for f in fs):
        return severity, False
    months = metrics.get("months_since_last_mention")
    if months is None or months <= st["months"]:
        return severity, False
    if metrics.get("latest_status") in st.get("exempt_statuses", []):
        return severity, False
    cap = st["cap"]
    if SEVERITY_RANK[severity] > SEVERITY_RANK[cap]:
        return cap, True
    return severity, True


# ---- exposure -------------------------------------------------------------------------

def _money(v: float) -> str:
    return f"${round(v / 100) * 100:,.0f}" if v >= 10000 else f"${v:,.0f}"


def exposure_for(spec: dict[str, Any], metrics: dict[str, Any], building: dict[str, Any], taxonomy: dict[str, Any]) -> tuple[str, str, str]:
    """Returns (agent exposure line, client exposure line, calculation note)."""
    nd = taxonomy.get("not_determinable", "Not determinable from these documents")
    ex = spec.get("exposure", {"method": "none"})
    method = ex.get("method", "none")
    if method == "none":
        return "—", "", ""
    if method == "not_determinable":
        return nd, "", ""
    if method == "fixed":
        return ex.get("text", nd), "", ""
    if method == "per_event_deductible":
        v = metrics.get(ex.get("from", "water_deductible"))
        return (f"Up to {_money(v)} per event", f"Up to {_money(v)}", f"water damage deductible {_money(v)}") if v else (nd, "", "")
    if method == "fixed_from_metric":
        v = metrics.get(ex.get("from"))
        return (f"{_money(v)} owing", f"{_money(v)} owing", "") if v else ("—", "", "")
    if method in ("unit_share_of_range", "unit_share_of_range_or_not_determinable"):
        lo_k, hi_k = ex.get("from", ["min_building_cost", "max_building_cost"])
        lo, hi = metrics.get(lo_k), metrics.get(hi_k)
        try:
            ue = float(str(building.get("unit_entitlement", "")).replace(",", ""))
            tot = float(str(building.get("total_unit_entitlement", "")).replace(",", ""))
        except ValueError:
            ue = tot = 0.0
        if lo is None or hi is None or not ue or not tot:
            missing = []
            if lo is None or hi is None:
                missing.append("no building-level cost range")
            if not ue:
                missing.append("unit entitlement not on the Form B")
            if not tot:
                missing.append("total unit entitlement not stated")
            return nd, "", "; ".join(missing)
        share_lo, share_hi = lo * ue / tot, hi * ue / tot
        calc = f"{_money(lo)} – {_money(hi)} × {ue:,.0f}/{tot:,.0f} entitlement = {_money(share_lo)} – {_money(share_hi)}"
        agent = f"{_money(share_lo)} – {_money(share_hi)}" if share_lo != share_hi else _money(share_lo)
        client = f"Roughly {_money(round(share_lo, -3))} – {_money(round(share_hi, -3))}" if share_lo != share_hi else f"Roughly {_money(round(share_lo, -3))}"
        return agent, client, calc
    return nd, "", ""


# ---- assemble -------------------------------------------------------------------------

def build_threads(facts: list[Fact], sections: list[Section], building: dict[str, Any], taxonomy: dict[str, Any],
                  review_date: date | None = None) -> list[Thread]:
    review_date = review_date or date.today()
    facts_by_id = {f.fact_id: f for f in facts}
    dated = [f.date for f in facts if f.date and f.citations and f.citations[0].kind == "page"]
    dated += [s.meeting_date for s in sections if s.meeting_date]
    latest_doc = date.fromisoformat(max(dated)) if dated else None
    threads = group_threads(facts, taxonomy)
    for t in threads:
        spec = taxonomy["categories"][t.category]
        m = metrics_for(t, facts, review_date, latest_doc, building, sections)
        sev, crit = rule_severity(spec, m)
        sev2, stale = apply_staleness(t, m, sev, taxonomy, facts_by_id)
        m["stale_single_mention"] = stale
        if stale and sev2 != sev:
            crit = f"{crit} Capped at {sev2}: single mention {m['months_since_last_mention']} months before the latest document, no open action."
        agent_exp, client_exp, calc = exposure_for(spec, m, building, taxonomy)
        m["exposure"], m["client_exposure"], m["exposure_calc"] = agent_exp, client_exp, calc
        t.metrics = m
        t.latest_status = m.get("latest_status")
        t.rule_severity = sev2
        t.rule_criteria = crit
    return threads


def absence_citation(thread: Thread, taxonomy: dict[str, Any]) -> Citation | None:
    """For an envelope recommendation with no completion record: cite the minutes searched."""
    m = thread.metrics
    if thread.category != "building_envelope" or not m.get("has_remediation_recommendation") or m.get("has_completion_record"):
        return None
    ms = m.get("minutes_searched") or {}
    if not ms.get("n"):
        return Citation(source_doc="(package)", page_number=None, kind="absence", label="No council minutes supplied to check for follow-up")
    from .extract import _fmt_date
    return Citation(source_doc=", ".join(ms["files"]), page_number=None, kind="absence",
                    label=f"Absence across {ms['n']} council/general meeting minutes, {_fmt_date(ms['from'])} – {_fmt_date(ms['to'])}")
