import pytest

from strata_review import report
from strata_review.schemas import CategoryStatus, Citation, Flag, PackageSummary, ReviewResult
from strata_review.settings import load_settings

S = load_settings(llm_mode="mock")


def _result(flags):
    return ReviewResult(building={"address": "2135 Springer Avenue, Burnaby, BC", "strata_plan": "BCS3392"}, unit="1204",
                        package=PackageSummary(n_docs=1, n_pages=2, n_scanned=0, n_low_ocr=0, files=["m.pdf"]),
                        sections=[], facts=[], discarded=[], threads=[], flags=flags,
                        category_status=[CategoryStatus(category="special_levies", label="Special levies", state="flagged", severity="red")],
                        questions=["Q1"], overall_risk="Raised", exposure_total="$14,200 – $17,100")


def _flag(**over):
    base = dict(flag_id="flag01", category="special_levies", thread_id="t001", severity="red", title="Roof", agent_text="Agent text.",
                client_title="Roof for buyers", client_text="Client text.", exposure="$14,200 – $17,100", client_exposure="Roughly $14,000 – $17,000",
                citations=[Citation(source_doc="m.pdf", page_number=3, label="Council minutes, 8 Apr 2026, p. 3")],
                client_citations=[Citation(source_doc="m.pdf", page_number=None, label="Council meeting notes, April 2026")],
                rule_severity="red", rule_criteria="crit", judge_severity="amber", judge_rationale="Model thinks amber.", judge_disagreed=True)
    base.update(over)
    return Flag.model_construct(**base)   # model_construct skips validation so an empty citation list can reach the template


def test_template_refuses_flag_with_empty_citations():
    res = _result([_flag(citations=[])])
    with pytest.raises(report.ReportRefused, match="flag01.*no citations"):
        report.render(res, S.tenant, S.taxonomy, "agent")
    with pytest.raises(report.ReportRefused):
        report.render(res, S.tenant, S.taxonomy, "client")   # client falls back to agent citations, which are empty too


def test_disagreement_renders_on_agent_only_and_client_shows_plain_language():
    res = _result([_flag()])
    agent = report.render(res, S.tenant, S.taxonomy, "agent")
    client = report.render(res, S.tenant, S.taxonomy, "client")
    assert "Model cross-check disagreed" in agent and "Model thinks amber." in agent
    assert "cross-check" not in client and "Model thinks amber." not in client and "Rule:" not in client
    assert "Roof for buyers" in client and "Client text." in client and "Council meeting notes, April 2026" in client
    assert "Appendix" in agent and "Appendix" not in client
    assert "Suite 1204, 2135 Springer Avenue" in agent


def test_client_collapses_notes_to_one_quiet_line():
    flags = [_flag(), _flag(flag_id="flag02", category="litigation", severity="note", title="No litigation", client_title="No disputes",
                           judge_disagreed=False, judge_severity="note", rule_severity="note")]
    res = _result(flags)
    res.category_status.append(CategoryStatus(category="litigation", label="Litigation", state="flagged", severity="note"))
    client = report.render(res, S.tenant, S.taxonomy, "client")
    assert "Also checked, nothing found: legal disputes." in client
    assert "No disputes" not in client          # the note flag is not rendered as a card
    agent = report.render(res, S.tenant, S.taxonomy, "agent")
    assert "No litigation" in agent             # agent still sees it
