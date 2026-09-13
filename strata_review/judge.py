"""Stage 4b: severity judgment cross-check and flag assembly.

The config rule (threads.py) decides the severity. The judge model (Opus) sees each thread's
timeline, metrics and the category's rules and returns its own severity, a rationale and the
report narratives. Code then:
  * keeps the rule severity as the flag's severity;
  * records the judge severity and rationale on the thread and the flag; if they differ the
    flag is marked judge_disagreed and the report shows both;
  * checks every dollar figure in the narratives against the thread's facts and derived
    exposure figures. A narrative with an unsupported figure is replaced by the deterministic
    template narrative and the substitution is logged.
The template narratives are also what the offline mock produces.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .extract import _fmt_date, client_cite_label
from .llm import LLM
from .schemas import CategoryStatus, Citation, Fact, Flag, Section, Thread, Usage
from .threads import SEVERITY_RANK, absence_citation

log = logging.getLogger(__name__)

JUDGE_SYSTEM = """You are the cross-check on a strata (condo) document review for a buyer in British
Columbia. You receive issue threads assembled from the documents: each has a dated timeline of
extracted facts (every one anchored to a source page), computed metrics, the category's severity
rules as written in the configuration, and the severity those rules produced.

For each thread:
1. Give your own severity (red, amber or note) applying the same rules. If you depart from the
   rule severity, say exactly why in the rationale, citing facts in the timeline. Departures are
   recorded and shown to the reviewer; they do not override the rule.
2. Write the agent narrative: two to four sentences for a realtor, precise, with dates and
   figures drawn only from the timeline. Never introduce a dollar figure, date or outcome that is
   not in the facts or the metrics. Where the documents cannot answer something, say so in those
   words: "not determinable from these documents".
3. Write the client narrative: plain language for a buyer, no jargon, same honesty rule.
4. Titles: a short factual headline for each version.

Absence of evidence is absence, not clearance. An engineer's recommendation with no recorded
follow-up means completion is not determinable from these documents; it does not mean the work
was not done, and it does not mean it was.

Finally list four to six questions the buyer's agent should put to the listing agent, each tied
to a thread.
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["red", "amber", "note"]},
                    "rationale": {"type": "string"},
                    "title": {"type": "string"},
                    "agent_text": {"type": "string"},
                    "client_title": {"type": "string"},
                    "client_text": {"type": "string"},
                },
                "required": ["thread_id", "severity", "rationale", "title", "agent_text", "client_title", "client_text"],
                "additionalProperties": False,
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["judgments", "questions"],
    "additionalProperties": False,
}


def _money(v: float | None) -> str:
    if v is None:
        return "an unstated amount"
    return f"${v:,.0f}"


def _range(lo: float | None, hi: float | None) -> str:
    if lo is None and hi is None:
        return "an unstated amount"
    if lo is None or hi is None or lo == hi:
        return _money(hi if lo is None else lo)
    return f"{_money(lo)} – {_money(hi)}"


STATUS_WORDS = {
    "discussed": "discussed", "quotes_requested": "quotes requested", "quotes_received": "quotes received",
    "proposed": "proposed", "deferred": "deferred", "approved": "approved", "tendered": "tendered",
    "completed": "completed", "recommended": "recommended", "none": "none",
}


def thread_payload(thread: Thread, facts_by_id: dict[str, Fact], spec: dict[str, Any]) -> dict[str, Any]:
    timeline = []
    for fid in thread.fact_ids:
        f = facts_by_id[fid]
        timeline.append({"date": f.date, "doc": f.doc_type, "kind": f.kind, "status": f.status, "summary": f.summary,
                         "amount_min": f.amount_min, "amount_max": f.amount_max, "cite": f.citations[0].label,
                         "quote": f.data.get("quote", "")[:160]})
    return {"thread_id": thread.thread_id, "category": thread.category, "label": spec["label"], "topic": thread.topic,
            "timeline": timeline, "metrics": {k: v for k, v in thread.metrics.items() if k != "evidence_fact_ids"},
            "rules": [{"level": r["level"], "criteria": r["criteria"]} for r in spec["severity"]],
            "rule_severity": thread.rule_severity, "rule_criteria": thread.rule_criteria}


# ---- template narratives (mock output and fallback) ------------------------------------

def template_narrative(thread: Thread, facts_by_id: dict[str, Fact], spec: dict[str, Any], building: dict[str, Any]) -> dict[str, str]:
    m = thread.metrics
    fs = [facts_by_id[i] for i in thread.fact_ids]
    cat = thread.category
    nd = "not determinable from these documents"
    if cat == "special_levies":
        work = [f for f in fs if f.kind in ("major_work_discussed", "quote_obtained", "work_deferred", "levy_proposed", "levy_passed")]
        steps = []
        for f in work:
            if f.status in ("quotes_received", "deferred", "approved", "proposed", "quotes_requested"):
                s = f"{_fmt_date(f.date)}: {STATUS_WORDS.get(f.status, f.status)}"
                if f.amount_max is not None and f.status in ("quotes_received", "deferred", "approved"):
                    s += f" ({_range(f.amount_min, f.amount_max)})"
                steps.append(s)
        seen, dedup = set(), []
        for s in steps:
            if s not in seen:
                seen.add(s); dedup.append(s)
        latest = m.get("latest_status")
        none_stated = any(f.kind == "levy_none_stated" for f in fs)
        state = {"approved": "special levy approved", "deferred": "quotes obtained, decision deferred, no levy passed yet",
                 "quotes_received": "quotes obtained, no levy passed yet", "proposed": "levy proposed, not yet voted on",
                 "quotes_requested": "work identified, quotes being sought", "none": "no levy approved"}.get(latest, "under discussion")
        title = f"{thread.topic.capitalize()}: {state}" if work else "No special levy approved or proposed"
        if not work:
            return {"title": title, "agent_text": "The Form B and financial statements record no approved special levy and no resolution pending.",
                    "client_title": "No one-time charges approved", "client_text": "The official certificate records no special charges approved against the unit."}
        agent = f"The minutes track {thread.topic} through {len(work)} mention(s): " + "; ".join(dedup) + ". "
        if m.get("max_building_cost"):
            agent += f"The building-level range on the latest priced mention is {_range(m['min_building_cost'], m['max_building_cost'])} ({m.get('levy_range_source')}). "
        if none_stated:
            agent += "No levy has been approved, so nothing appears on the Form B; a buyer reading only that document would see nothing. "
        if m.get("exposure_calc"):
            agent += f"At this unit's entitlement the share would be {m['exposure']} ({m['exposure_calc']})."
        else:
            agent += f"Unit share is {nd}: {m.get('exposure') or nd}."
        client_title = "The building is likely heading for a large one-time charge" if latest in ("deferred", "quotes_received", "proposed", "approved") else "Possible future repair charge"
        client = (f"The strata council has been dealing with {thread.topic}. " +
                  (f"Quotes for the whole building came in at {_range(m['min_building_cost'], m['max_building_cost'])}. " if m.get("max_building_cost") else "") +
                  ("Nothing has been voted on yet, so it does not show on any official form. " if latest != "approved" and none_stated else "") +
                  (f"If it goes ahead and is paid for by a special levy, your share would land at {m['client_exposure'].lower()}." if m.get("client_exposure") else f"What your share would be is {nd}."))
        return {"title": title, "agent_text": agent.strip(), "client_title": client_title, "client_text": client.strip()}
    if cat == "contingency_reserve":
        bal, rec, ratio = m.get("crf_balance"), m.get("crf_recommended"), m.get("reserve_ratio")
        title = f"Reserve fund at {ratio:.0%} of the recommended balance" if ratio is not None else ("Reserve fund balance with no recommendation to compare against" if bal else "Reserve fund position not stated")
        agent = ""
        if rec:
            agent += f"The depreciation report recommends a contingency balance of {_money(rec)}" + (f" by {m['crf_target_year']}" if m.get("crf_target_year") else "") + ". "
        if bal:
            agent += f"The most recent balance in the documents is {_money(bal)}" + (f" as at {_fmt_date(m['crf_balance_date'])}" if m.get("crf_balance_date") else "") + \
                     (f" against annual contributions of {_money(m['crf_contribution'])}" if m.get("crf_contribution") else "") + ". "
        if ratio is not None and ratio < 0.5:
            agent += "The gap is wide enough that major work will be funded by levy rather than from reserves."
        elif ratio is None:
            agent += "Without a recommended figure the adequacy of the balance is " + nd + "."
        client = ("Stratas keep a savings fund for big repairs. " +
                  (f"A professional report said this building should have about {_money(rec)} saved" + (f" by {m['crf_target_year']}" if m.get("crf_target_year") else "") + ". " if rec else "") +
                  (f"It currently has around {_money(bal)}" + (f" and adds roughly {_money(m['crf_contribution'])} a year" if m.get("crf_contribution") else "") + ". " if bal else "") +
                  ("That shortfall is why repairs here are more likely to be charged to owners directly rather than paid from savings." if ratio is not None and ratio < 0.5 else ""))
        return {"title": title, "agent_text": agent.strip(), "client_title": "The building's savings fund is well short of what it needs" if ratio is not None and ratio < 0.5 else "The building's savings fund", "client_text": client.strip()}
    if cat == "building_envelope":
        recs = [f for f in fs if f.kind == "envelope_recommendation"]
        if m.get("has_remediation_recommendation") and not m.get("has_completion_record"):
            r = min(recs, key=lambda f: (f.doc_type != "engineering_report", f.date or ""))
            ms = m.get("minutes_searched") or {}
            yr = m.get("recommendation_date", "")[:4] if m.get("recommendation_date") else ""
            title = f"Envelope remediation recommended in {yr}, completion not determinable" if yr else "Envelope remediation recommended, completion not determinable"
            agent = (f"A building envelope assessment dated {_fmt_date(r.date)} recommended remediation" +
                     (f" within the timeframe stated ({r.data.get('timeframe')})" if r.data.get("timeframe") else "") +
                     (f", with an opinion of cost of {_range(r.amount_min, r.amount_max)} at that date" if r.amount_max else "") + ". " +
                     (f"None of the {ms.get('n')} sets of minutes supplied ({_fmt_date(ms.get('from'))} to {_fmt_date(ms.get('to'))}) records the work being tendered or completed. " if ms.get("n") else "No minutes were supplied in which follow-up could be checked. ") +
                     "This may mean the work was done before the minutes period begins, or that it remains outstanding. Completion is " + nd + ".")
            client = (f"In {yr or 'an earlier year'} an engineer found problems with the building's exterior and recommended repairs. "
                      "Nothing in the records we were given says whether that work happened. It may well have been done. We cannot tell from these documents, and it is worth asking before you go firm.")
            return {"title": title, "agent_text": agent, "client_title": "An unresolved question about the exterior", "client_text": client}
        n = m.get("water_ingress_mentions", 0)
        title = f"{thread.topic.capitalize()}: {n} water ingress mention(s)" if n else f"{thread.topic.capitalize()} recorded"
        agent = "; ".join(f"{_fmt_date(f.date)}: {f.summary[:110]}" for f in fs[:4]) + ("." if fs else "")
        if m.get("has_completion_record"):
            agent += " A completion record exists in the documents."
        return {"title": title, "agent_text": agent, "client_title": "Water or exterior issues noted in the records",
                "client_text": f"The records mention {thread.topic} {n or len(fs)} time(s). Ask whether the cause has been fixed."}
    if cat == "depreciation_report_currency":
        age, rd = m.get("report_age_years"), m.get("report_date")
        if rd:
            title = f"Depreciation report is {age:.0f} years old" if age is not None and age >= 1 else "Depreciation report is current"
            agent = f"The most recent depreciation report dates from {_fmt_date(rd, short=False)}. " + ("Cost estimates within it predate recent construction cost movement, so figures drawn from it should be treated as a floor rather than a forecast." if age and age >= 3 else "")
            client = f"The building's long-term repair forecast was written in {rd[:4]}. " + ("Costs have moved since then, so treat its figures as a minimum." if age and age >= 3 else "")
        else:
            title, agent, client = "Depreciation report date not stated", "No dated depreciation report was found in the documents supplied.", "We could not find a dated long-term repair forecast in the documents."
        return {"title": title, "agent_text": agent.strip(), "client_title": "How current the repair forecast is", "client_text": client.strip()}
    if cat == "insurance_deductibles":
        wd = m.get("water_deductible")
        title = f"Water damage deductible at {_money(wd)}" if wd else "Water damage deductible not stated"
        agent = (f"The policy carries a water damage deductible of {_money(wd)} per occurrence" + (f", increased from {_money(m['deductible_change'][0])}" if m.get("deductible_change") else "") + ". " if wd else "The insurance summary does not state a water damage deductible. ") + \
                ("Under the bylaws an owner may be pursued for the deductible where a claim originates in their strata lot." if m.get("has_chargeback_bylaw") else "Whether the strata can charge the deductible back to an owner depends on the bylaws in force.")
        client = (f"If water damage starts in your unit, you could be responsible for a deductible of up to {_money(wd)} depending on the bylaws. " if wd else "The size of the water damage deductible is not stated in the documents. ") + \
                 "This is worth raising with your insurance broker, because personal coverage can be arranged for exactly this."
        return {"title": title, "agent_text": agent, "client_title": "The insurance deductible for water damage is high" if wd and wd >= 50000 else "Insurance deductibles", "client_text": client}
    if cat == "litigation":
        if m.get("has_active_litigation"):
            title, ctitle = "Active legal proceeding involving the strata", "The strata is involved in a legal case"
        elif m.get("has_threatened_litigation"):
            title, ctitle = "Dispute recorded, no proceeding filed", "A dispute is mentioned in the records"
        else:
            title, ctitle = "No litigation recorded", "No legal disputes recorded"
        agent = "; ".join(f"{f.summary}" for f in fs[:4]) + "."
        return {"title": title, "agent_text": agent, "client_title": ctitle, "client_text": "The documents " + ("record a legal matter involving the building." if m.get("has_active_litigation") or m.get("has_threatened_litigation") else "state that the strata is not in any court or tribunal case.")}
    if cat == "bylaws":
        parts = []
        if m.get("has_rental_restriction"):
            parts.append("a rental restriction bylaw remains in the consolidated bylaws despite the November 2022 provincial changes; whether it is enforceable is a legal question outside what these documents can settle")
        if m.get("has_age_restriction"):
            parts.append("an age restriction bylaw is in force")
        if m.get("has_str_restriction"):
            parts.append("short-term rentals under 30 days are prohibited")
        if m.get("has_pet_restriction"):
            parts.append("pets are limited by bylaw")
        title = "Rental restriction bylaw remains on the books" if m.get("has_rental_restriction") else ("Age restriction bylaw in force" if m.get("has_age_restriction") else "Bylaws restrict pets and short stays")
        agent = ("; ".join(parts).capitalize() + ".") if parts else "No rental, pet, age or short-term rental restrictions were found in the bylaws supplied."
        client = ("Renting the unit out may be restricted by an old bylaw; a lawyer should confirm whether it still applies. " if m.get("has_rental_restriction") else "") + \
                 ("Short stays under 30 days are not allowed. " if m.get("has_str_restriction") else "") + ("Pets are limited. " if m.get("has_pet_restriction") else "")
        return {"title": title, "agent_text": agent, "client_title": "Rules on renting, pets and short stays", "client_text": client.strip() or "No rules on renting, pets or short stays were found."}
    if cat == "parking_storage":
        words = {"lcp": "limited common property", "cp": "common property allocated by council", "leased": "leased", "none": "not allocated"}
        pk, st = m.get("parking_designation"), m.get("storage_designation")
        title = "Parking and storage designation stated on the Form B" if pk and st else "Parking or storage designation not stated"
        agent = f"Parking: {words.get(pk, pk or 'not stated')}. Storage: {words.get(st, st or 'not stated')}. " + ("A council-allocated locker can be reassigned on notice." if st == "cp" else "")
        client = ("Your parking stall is tied to the unit on the strata plan. " if pk == "lcp" else "Check how the parking stall is assigned. ") + ("The storage locker is assigned by the council and could in principle be reassigned." if st == "cp" else "")
        return {"title": title, "agent_text": agent.strip(), "client_title": "Parking and storage", "client_text": client.strip()}
    if cat == "form_b_unit":
        ar = m.get("arrears_amount")
        title = f"{_money(ar)} owing on this strata lot" if ar else "Nothing owing on this strata lot"
        agent = (f"The Form B dated {_fmt_date(m['form_b_date'])} " if m.get("form_b_date") else "The Form B ") + \
                (f"records {_money(ar)} owing. " if ar else "records no amount owing. ") + (f"Monthly fees are {_money(m['monthly_fees'])}. " if m.get("monthly_fees") else "") + \
                (f"The certificate is {m['form_b_age_days']} days old; Form B figures are only reliable for a short period." if m.get("form_b_age_days") is not None and m["form_b_age_days"] > 60 else "")
        client = ("The seller owes the strata money on this unit; this should be cleared before completion." if ar else "The seller does not owe the strata anything on this unit as of the certificate date.")
        return {"title": title, "agent_text": agent.strip(), "client_title": "Amounts owing on this unit", "client_text": client}
    return {"title": thread.topic.capitalize(), "agent_text": "; ".join(f.summary for f in fs[:3]), "client_title": thread.topic.capitalize(), "client_text": ""}


def template_questions(flags: list[Flag]) -> list[str]:
    qs = []
    for fl in flags:
        if fl.severity == "note":
            continue
        qs.append({
            "special_levies": "Has the strata obtained updated quotes for the work in the minutes, and is a special levy resolution on the agenda for the next general meeting?",
            "contingency_reserve": "Is there a funding plan for the gap between the contingency balance and the depreciation report recommendation?",
            "building_envelope": "Has the envelope remediation recommended by the engineer been completed, and is the documentation available?",
            "insurance_deductibles": "What is the current water damage deductible, and has the strata considered a bylaw shifting responsibility to owners?",
            "bylaws": "Has the strata obtained legal advice on the rental restriction bylaw, and is an amendment planned?",
            "depreciation_report_currency": "When is the next depreciation report update scheduled, and has it been budgeted?",
            "litigation": "What is the status of the legal matter recorded in the documents?",
            "parking_storage": "Can the parking and storage designation for this unit be confirmed in writing?",
            "form_b_unit": "Can an updated Form B be provided before subject removal?",
        }.get(fl.category, f"Please clarify the {fl.category.replace('_', ' ')} item raised in the minutes."))
    seen, out = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q); out.append(q)
    return out[:6]


# ---- honesty check on narrative numbers ----------------------------------------------

_DOLLARS = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(k|m|million|thousand)?", re.I)


def allowed_numbers(thread: Thread, facts_by_id: dict[str, Fact]) -> set[int]:
    nums: set[int] = set()
    for fid in thread.fact_ids:
        f = facts_by_id[fid]
        for v in (f.amount_min, f.amount_max):
            if v is not None:
                nums.add(int(round(v)))
    for k, v in thread.metrics.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in ("n_mentions", "reserve_ratio", "report_age_years", "months_since_last_mention", "form_b_age_days"):
            nums.add(int(round(v)))
        if isinstance(v, tuple):
            nums.update(int(round(x)) for x in v if isinstance(x, (int, float)))
    for key in ("exposure", "client_exposure", "exposure_calc"):
        for m in _DOLLARS.finditer(str(thread.metrics.get(key, ""))):
            nums.add(int(float(m.group(1).replace(",", ""))))
    return nums


def unsupported_numbers(text: str, allowed: set[int]) -> list[str]:
    bad = []
    for m in _DOLLARS.finditer(text):
        v = float(m.group(1).replace(",", ""))
        suf = (m.group(2) or "").lower()
        if suf == "k" or suf == "thousand":
            v *= 1000
        elif suf in ("m", "million"):
            v *= 1_000_000
        iv = int(round(v))
        # accept rounding to the nearest $100 / $1,000 of an allowed figure
        if iv in allowed or any(abs(iv - a) <= max(1000, a * 0.02) for a in allowed):
            continue
        bad.append(m.group(0))
    return bad


# ---- flag assembly --------------------------------------------------------------------

def _dedupe(cites: list[Citation]) -> list[Citation]:
    seen, out = set(), []
    for c in cites:
        k = (c.source_doc, c.page_number, c.label)
        if k not in seen:
            seen.add(k); out.append(c)
    return out


def flag_citations(thread: Thread, facts_by_id: dict[str, Fact], sections_by_id: dict[str, Section], taxonomy: dict[str, Any],
                   max_cites: int = 6) -> tuple[list[Citation], list[Citation]]:
    ev = [facts_by_id[i] for i in thread.metrics.get("evidence_fact_ids", thread.fact_ids)]
    ev.sort(key=lambda f: (f.date or ""), reverse=True)
    agent = _dedupe([c for f in ev for c in f.citations])[:max_cites]
    client = []
    seen = set()
    for f in ev:
        s = sections_by_id.get(f.section_id)
        lbl = client_cite_label(s, f.date) if s else f.citations[0].label
        if lbl not in seen:
            seen.add(lbl); client.append(Citation(source_doc=f.citations[0].source_doc, page_number=None, label=lbl))
    ab = absence_citation(thread, taxonomy)
    if ab:
        agent.append(ab)
        client.append(Citation(source_doc=ab.source_doc, page_number=None, kind="absence", label="No follow-up found in the council meeting notes supplied"))
    return agent, client[:4]


def judge_threads(threads: list[Thread], facts: list[Fact], sections: list[Section], building: dict[str, Any], taxonomy: dict[str, Any],
                  llm: LLM, model: str) -> tuple[list[Flag], list[str], list[Usage]]:
    facts_by_id = {f.fact_id: f for f in facts}
    sections_by_id = {s.section_id: s for s in sections}
    cats = taxonomy["categories"]
    payload = {"building": {k: v for k, v in building.items() if not k.endswith("_cite")},
               "threads": [thread_payload(t, facts_by_id, cats[t.category]) for t in threads]}
    user = "THREADS:\n" + json.dumps(payload, indent=1)
    result, usage = llm.complete_json(task="assign_severity", model=model, system=JUDGE_SYSTEM, user=user, schema=JUDGE_SCHEMA, max_tokens=16000)
    by_thread = {j.get("thread_id"): j for j in result.get("judgments", [])}
    flags: list[Flag] = []
    for i, t in enumerate(threads, start=1):
        spec = cats[t.category]
        j = by_thread.get(t.thread_id, {})
        judge_sev = j.get("severity") if j.get("severity") in SEVERITY_RANK else None
        t.judge_severity = judge_sev
        t.judge_rationale = j.get("rationale") or ""
        disagreed = judge_sev is not None and judge_sev != t.rule_severity
        if disagreed:
            log.warning("judge disagreed on %s (%s): rule=%s judge=%s: %s", t.thread_id, t.category, t.rule_severity, judge_sev, t.judge_rationale[:120])
        tpl = template_narrative(t, facts_by_id, spec, building)
        narrative, source = tpl, "template"
        if j.get("agent_text") and j.get("client_text"):
            allowed = allowed_numbers(t, facts_by_id)
            bad = unsupported_numbers(j["agent_text"] + " " + j["client_text"] + " " + j.get("title", "") + " " + j.get("client_title", ""), allowed)
            if bad:
                log.warning("narrative for %s used unsupported figures %s; using template", t.thread_id, bad)
            else:
                narrative = {"title": j.get("title") or tpl["title"], "agent_text": j["agent_text"],
                             "client_title": j.get("client_title") or tpl["client_title"], "client_text": j["client_text"]}
                source = "model"
        agent_c, client_c = flag_citations(t, facts_by_id, sections_by_id, taxonomy)
        if not agent_c:
            log.error("thread %s has no citations; flag suppressed", t.thread_id)
            continue
        flags.append(Flag(
            flag_id=f"flag{i:02d}", category=t.category, thread_id=t.thread_id, severity=t.rule_severity or "note",
            title=narrative["title"], agent_text=narrative["agent_text"], client_title=narrative["client_title"], client_text=narrative["client_text"],
            exposure=t.metrics.get("exposure", "—"), client_exposure=t.metrics.get("client_exposure", ""), exposure_calc=t.metrics.get("exposure_calc", ""),
            citations=agent_c, client_citations=client_c, rationale=t.rule_criteria or "", rule_severity=t.rule_severity, rule_criteria=t.rule_criteria or "",
            judge_severity=judge_sev, judge_rationale=t.judge_rationale, judge_disagreed=disagreed, narrative_source=source,
            stale=bool(t.metrics.get("stale_single_mention")),
        ))
    order = {c: i for i, c in enumerate(taxonomy.get("report_order", list(cats)))}
    flags.sort(key=lambda f: (-SEVERITY_RANK[f.severity], order.get(f.category, 99), f.flag_id))
    questions = [q for q in result.get("questions", []) if isinstance(q, str) and q.strip()][:6] or template_questions(flags)
    return flags, questions, [usage]


def category_status(flags: list[Flag], sections: list[Section], docs_n: int, taxonomy: dict[str, Any]) -> list[CategoryStatus]:
    present = {s.doc_type for s in sections}
    out = []
    for cat in taxonomy.get("report_order", list(taxonomy["categories"])):
        spec = taxonomy["categories"][cat]
        cf = [f for f in flags if f.category == cat]
        if cf:
            top = max(cf, key=lambda f: SEVERITY_RANK[f.severity])
            out.append(CategoryStatus(category=cat, label=spec["label"], state="flagged", severity=top.severity))
        elif not any(dt in present for dt in spec.get("doc_types", [])):
            out.append(CategoryStatus(category=cat, label=spec["label"], state="absent", text=spec.get("absence")))
        else:
            primary = spec["doc_types"][0].replace("_", " ")
            if spec["doc_types"][0] not in present:
                out.append(CategoryStatus(category=cat, label=spec["label"], state="absent", text=spec.get("absence")))
            else:
                out.append(CategoryStatus(category=cat, label=spec["label"], state="none_found",
                                          text=f"No {spec['label'].lower()} matters found in the {docs_n} documents supplied. Absence of a mention is not confirmation."))
    return out


def overall_risk(flags: list[Flag]) -> str:
    reds = sum(f.severity == "red" for f in flags)
    ambers = sum(f.severity == "amber" for f in flags)
    if reds >= 2:
        return "Elevated"
    if reds == 1:
        return "Raised"
    if ambers:
        return "Moderate"
    return "Low"


def exposure_total(flags: list[Flag], taxonomy: dict[str, Any]) -> str:
    lo = hi = 0.0
    found = False
    for f in flags:
        if f.exposure_calc and "=" in f.exposure_calc:
            nums = [float(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", f.exposure_calc.split("=")[-1])]
            if len(nums) >= 1:
                lo += nums[0]; hi += nums[-1]; found = True
    if not found:
        return taxonomy.get("not_determinable", "Not determinable from these documents")
    return f"${round(lo/100)*100:,.0f} – ${round(hi/100)*100:,.0f}" if lo != hi else f"${round(lo/100)*100:,.0f}"
