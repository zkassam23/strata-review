"""Thread grouping, metrics, config rules, staleness, exposure and the judge cross-check."""
from datetime import date

import pytest

from strata_review import judge, pipeline, threads
from strata_review.llm import MockLLM
from strata_review.schemas import Citation, Fact, Section, Usage
from strata_review.settings import load_settings

TAX = load_settings(llm_mode="mock").taxonomy


def _f(fid, cat, kind, topic, d, status=None, amin=None, amax=None, doc="council_minutes", page=1, data=None):
    return Fact(fact_id=fid, category=cat, kind=kind, summary=f"{kind} {topic}", section_id="s1", doc_type=doc,
                citations=[Citation(source_doc="m.pdf", page_number=page, label=f"Council minutes, {d}, p. {page}")],
                date=d, topic=topic, status=status, amount_min=amin, amount_max=amax, data=data or {})


def _minutes_sections(dates):
    return [Section(section_id=f"s{i}", doc_id="d1", source_file="m.pdf", doc_type="council_minutes", page_start=i, page_end=i,
                    confidence=0.9, meeting_date=d) for i, d in enumerate(dates, start=1)]


def test_topic_mentions_across_meetings_form_one_thread_and_separate_topics_do_not():
    facts = [
        _f("f1", "special_levies", "major_work_discussed", "roof membrane replacement", "2025-09-09", "quotes_requested"),
        _f("f2", "special_levies", "quote_obtained", "roof replacement quotes", "2025-11-08", "quotes_received", 780000, 940000),
        _f("f3", "special_levies", "work_deferred", "roof membrane replacement", "2026-04-08", "deferred", 780000, 940000),
        _f("f4", "special_levies", "major_work_discussed", "elevator modernisation", "2026-02-08", "discussed", 640000, 640000),
    ]
    ts = threads.group_threads(facts, TAX)
    by_topic = {t.topic: t for t in ts}
    assert set(by_topic) == {"roof membrane replacement", "elevator modernisation"}
    roof = by_topic["roof membrane replacement"]
    assert roof.fact_ids == ["f1", "f2", "f3"]           # chronological
    assert (roof.first_date, roof.last_date) == ("2025-09-09", "2026-04-08")


def test_roof_thread_rule_severity_and_exposure_calculation():
    facts = [
        _f("f2", "special_levies", "quote_obtained", "roof membrane replacement", "2025-11-08", "quotes_received", 780000, 940000),
        _f("f3", "special_levies", "work_deferred", "roof membrane replacement", "2026-04-08", "deferred", 780000, 940000),
    ]
    building = {"unit_entitlement": "182", "total_unit_entitlement": "10000"}
    ts = threads.build_threads(facts, _minutes_sections(["2025-11-08", "2026-04-08"]), building, TAX, review_date=date(2026, 9, 13))
    t = ts[0]
    assert t.rule_severity == "red" and t.metrics["latest_status"] == "deferred"
    assert t.metrics["max_building_cost"] == 940000
    assert t.metrics["exposure"] == "$14,200 – $17,100"
    assert t.metrics["exposure_calc"] == "$780,000 – $940,000 × 182/10,000 entitlement = $14,200 – $17,100"


@pytest.mark.parametrize("building", [{}, {"unit_entitlement": "182"}, {"total_unit_entitlement": "10000"}])
def test_exposure_is_not_determinable_without_entitlement(building):
    facts = [_f("f3", "special_levies", "work_deferred", "roof", "2026-04-08", "deferred", 780000, 940000)]
    t = threads.build_threads(facts, _minutes_sections(["2026-04-08"]), building, TAX)[0]
    assert t.metrics["exposure"] == "Not determinable from these documents"
    assert t.rule_severity == "red"       # severity does not depend on the exposure


def test_exposure_is_not_determinable_without_a_range():
    facts = [_f("f3", "special_levies", "work_deferred", "roof", "2026-04-08", "deferred")]
    t = threads.build_threads(facts, _minutes_sections(["2026-04-08"]), {"unit_entitlement": "182", "total_unit_entitlement": "10000"}, TAX)[0]
    assert t.metrics["exposure"] == "Not determinable from these documents"
    assert t.rule_severity == "amber"     # deferred with no cost


def test_reserve_ratio_rule():
    facts = [
        _f("b1", "contingency_reserve", "crf_balance", "reserve", "2025-12-31", amin=298412, amax=298412, doc="financial_statements"),
        _f("b2", "contingency_reserve", "crf_balance", "reserve", "2024-12-31", amin=237600, amax=237600, doc="financial_statements"),
        _f("r1", "contingency_reserve", "crf_recommended_balance", "reserve", "2022-06-15", "recommended", 1420000, 1420000, doc="depreciation_report", data={"target_year": "2027"}),
    ]
    t = threads.build_threads(facts, [], {}, TAX)[0]
    assert t.metrics["crf_balance"] == 298412 and t.metrics["reserve_ratio"] == 0.21 and t.rule_severity == "red"
    t2 = threads.build_threads(facts[:2], [], {}, TAX)[0]
    assert t2.metrics["reserve_ratio"] is None and t2.rule_severity == "amber"    # no recommendation to compare against


def test_envelope_recommendation_without_follow_up_is_red_and_says_not_determinable():
    facts = [_f("e1", "building_envelope", "envelope_recommendation", "building envelope sealant remediation", "2019-05-10", "recommended",
                340000, 420000, doc="engineering_report", data={"timeframe": "within five years", "deadline_year": "2024"})]
    secs = _minutes_sections(["2024-09-12", "2026-08-15"])
    t = threads.build_threads(facts, secs, {"unit_entitlement": "182", "total_unit_entitlement": "10000"}, TAX)[0]
    assert t.rule_severity == "red"
    assert t.metrics["completion"] == "not determinable from these documents"
    assert t.metrics["exposure"] == "Not determinable from these documents"   # never a share of a cost that may already be spent
    narrative = judge.template_narrative(t, {f.fact_id: f for f in facts}, TAX["categories"]["building_envelope"], {})
    assert "Completion is not determinable from these documents" in narrative["agent_text"]
    assert "not done" not in narrative["agent_text"] and "outstanding" in narrative["agent_text"]
    assert "It may well have been done" in narrative["client_text"]
    ab = threads.absence_citation(t, TAX)
    assert ab.kind == "absence" and ab.label == "Absence across 2 council/general meeting minutes, 12 Sep 2024 – 15 Aug 2026"


def test_envelope_with_completion_record_is_not_red():
    facts = [
        _f("e1", "building_envelope", "envelope_recommendation", "sealant remediation", "2019-05-10", "recommended", doc="engineering_report"),
        _f("e2", "building_envelope", "envelope_work_completed", "sealant remediation", "2025-03-15", "completed"),
    ]
    t = threads.build_threads(facts, _minutes_sections(["2025-03-15"]), {}, TAX)[0]
    assert t.rule_severity == "amber" and t.metrics["completion"] == "recorded"
    assert threads.absence_citation(t, TAX) is None


def test_stale_single_mention_is_capped_but_open_actions_are_not():
    secs = _minutes_sections(["2024-09-12", "2026-08-15"])
    stale = [_f("s1", "special_levies", "major_work_discussed", "parkade membrane", "2024-09-12", "discussed", 410000, 410000)]
    t = threads.build_threads(stale, secs, {}, TAX)[0]
    assert t.rule_severity == "note" and t.metrics["stale_single_mention"] is True and "Capped at note" in t.rule_criteria
    live = [_f("s2", "special_levies", "quote_obtained", "parkade membrane", "2024-09-12", "quotes_received", 410000, 410000)]
    t2 = threads.build_threads(live, secs, {}, TAX)[0]
    assert t2.rule_severity == "red" and t2.metrics["stale_single_mention"] is False
    recent = [_f("s3", "special_levies", "major_work_discussed", "parkade membrane", "2026-06-14", "discussed", 410000, 410000)]
    t3 = threads.build_threads(recent, secs, {}, TAX)[0]
    assert t3.rule_severity == "red" and t3.metrics["stale_single_mention"] is False


class _DisagreeingLLM:
    mocked = True

    def complete_json(self, *, task, model, system, user, schema, max_tokens=16000):
        import json
        payload = json.loads(user.split("THREADS:\n", 1)[1])
        js = []
        for t in payload["threads"]:
            js.append({"thread_id": t["thread_id"], "severity": "amber", "rationale": "Model view: quotes are preliminary.",
                       "title": "Roof quotes on the table", "agent_text": "Quotes of $780,000 to $940,000 were received; the decision was deferred.",
                       "client_title": "A roof bill is coming", "client_text": "Your share could be about $15,000."})
        return {"judgments": js, "questions": ["Is a roof levy on the AGM agenda?"]}, Usage(model=model, task=task, input_tokens=1, output_tokens=1, mocked=True)


class _FabricatingLLM(_DisagreeingLLM):
    def complete_json(self, **kw):
        payload, usage = super().complete_json(**kw)
        payload["judgments"][0]["agent_text"] = "Expect a levy of $2,000,000 across the building."   # not in any fact
        payload["judgments"][0]["severity"] = "red"
        return payload, usage


def _roof_state():
    facts = [
        _f("f2", "special_levies", "quote_obtained", "roof membrane replacement", "2025-11-08", "quotes_received", 780000, 940000, page=29),
        _f("f3", "special_levies", "work_deferred", "roof membrane replacement", "2026-04-08", "deferred", 780000, 940000, page=39),
    ]
    secs = _minutes_sections(["2025-11-08", "2026-04-08"])
    building = {"unit_entitlement": "182", "total_unit_entitlement": "10000"}
    ts = threads.build_threads(facts, secs, building, TAX, review_date=date(2026, 9, 13))
    return facts, secs, building, ts


def test_judge_disagreement_is_recorded_not_adopted():
    facts, secs, building, ts = _roof_state()
    flags, questions, _ = judge.judge_threads(ts, facts, secs, building, TAX, _DisagreeingLLM(), "judge-model")
    f = flags[0]
    assert f.severity == "red" and f.rule_severity == "red"         # rule is the authority
    assert f.judge_severity == "amber" and f.judge_disagreed is True
    assert "preliminary" in f.judge_rationale
    assert f.narrative_source == "model" and f.title == "Roof quotes on the table"   # figures were all supported
    assert questions == ["Is a roof levy on the AGM agenda?"]
    assert ts[0].judge_severity == "amber"


def test_narrative_with_unsupported_figure_falls_back_to_template():
    facts, secs, building, ts = _roof_state()
    flags, _, _ = judge.judge_threads(ts, facts, secs, building, TAX, _FabricatingLLM(), "judge-model")
    f = flags[0]
    assert f.narrative_source == "template"
    assert "$2,000,000" not in f.agent_text and "$14,200 – $17,100" in f.agent_text
    assert f.judge_disagreed is False          # judge said red, rule says red


def test_flags_always_carry_citations():
    facts, secs, building, ts = _roof_state()
    flags, _, _ = judge.judge_threads(ts, facts, secs, building, TAX, MockLLM(), "judge-model")
    assert [c.label for c in flags[0].citations] == ["Council minutes, 2026-04-08, p. 39", "Council minutes, 2025-11-08, p. 29"]
    with pytest.raises(Exception):
        judge.Flag(flag_id="x", category="special_levies", thread_id=None, severity="red", title="t", agent_text="a",
                   client_title="c", client_text="c", exposure="—", citations=[])


def test_synthetic_package_threads_produce_expected_severities(synth_package):
    settings = load_settings(llm_mode="mock")
    settings.extra["unit"] = "1204"
    st = pipeline.run(synth_package, settings=settings, stop_after="anchor")
    sev = {}
    for f in st.flags:
        sev.setdefault(f.category, set()).add(f.severity)
    assert "red" in sev["special_levies"] and "red" in sev["contingency_reserve"] and "red" in sev["building_envelope"]
    assert sev["insurance_deductibles"] == {"amber"} and sev["bylaws"] == {"amber"} and sev["depreciation_report_currency"] == {"amber"}
    assert sum(f.severity == "red" for f in st.flags) == 3
    assert sum(f.severity == "amber" for f in st.flags) >= 2
    roof = next(f for f in st.flags if f.category == "special_levies" and f.severity == "red")
    assert roof.exposure == "$14,200 – $17,100"
    assert not st.anchor_errors and all(f.citations for f in st.flags)
    env = next(f for f in st.flags if f.category == "building_envelope" and f.severity == "red")
    assert any(c.kind == "absence" for c in env.citations)
