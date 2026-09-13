"""End to end against the synthetic package: CLI entry point, outputs, honesty rules in the HTML."""
import json
import subprocess
import sys

from synth.generate import PLANTED


def test_cli_end_to_end_on_synthetic_package(synth_package, tmp_path):
    out = tmp_path / "report"
    cmd = [sys.executable, "-m", "strata_review.cli", str(synth_package), "--mock", "--unit", "1204",
           "--agent", "Kassam & Associates", "--brokerage", "eXp Realty Canada", "--out", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    flags = json.loads((out / "flags.json").read_text())
    by_cat = {}
    for f in flags["flags"]:
        by_cat.setdefault(f["category"], []).append(f["severity"])
    for cat in PLANTED["expected_red"]:
        assert "red" in by_cat[cat], cat
    for cat in PLANTED["expected_amber"]:
        assert "amber" in by_cat[cat], cat
    assert sum(f["severity"] == "red" for f in flags["flags"]) == 3
    assert sum(f["severity"] == "amber" for f in flags["flags"]) >= 2
    assert all(f["citations"] for f in flags["flags"])
    assert flags["exposure_total"] == "$14,200 – $17,100"

    agent = (out / "agent.html").read_text()
    client = (out / "client.html").read_text()
    assert "Kassam &amp; Associates" in agent and "eXp Realty Canada" in client
    assert "Minutes searched: 25 council and general meetings, 12 Sep 2024 to 15 Aug 2026" in agent
    assert "Pages with low OCR confidence (1)" in agent and "p. 10" in agent
    assert "Sections classified below the confidence threshold (1)" in agent
    assert "Completion is not determinable from these documents" in agent
    assert "It may well have been done" in client
    assert "Absence across 25 council/general meeting minutes" in agent
    assert "$14,200 – $17,100" in agent and "Roughly $14,000 – $17,000" in client
    assert "Model cross-check" not in client and "Appendix" not in client
    assert "Also checked, nothing found" in client
