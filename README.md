# Strata review

Document due diligence for BC condo transactions. Drop in a strata package (folder of PDFs, one
merged PDF, or a zip), get back an agent report with citations and exposure estimates and a
client report in plain language, both under the agent's own brand.

## Clone to running site

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng     # OCR for scanned pages (brew install tesseract on macOS)
git clone <this repo> strata-review && cd strata-review
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # then put your key in it: ANTHROPIC_API_KEY=sk-ant-...
strata-review-web               # http://127.0.0.1:8000
```

Without a key the tool runs in offline mock mode (keyword heuristics stand in for the models,
a banner says so). That is enough to see the pipeline and the pages, not to review a real building.

**Where the brand goes:** `config/tenant.yaml` (`agent_name`, `brokerage`, the two disclaimers). Every report
and every web page reads it; the CLI `--agent` and `--brokerage` flags override it for one run.

**Where the key goes:** `.env` at the repo root, line `ANTHROPIC_API_KEY=...`. It is read by
`strata_review/settings.py` at startup. Nothing else needs to change for a real run.

## CLI

```bash
python -m synth.generate                                   # synthetic test building in synth/package
strata-review synth/package --agent "Kassam & Associates" --brokerage "eXp Realty Canada" \
    --unit 1204 --out ./report                             # report/agent.html, client.html, flags.json
strata-review ./package --mock --stop-after classify      # stop after any stage and print its output
```

Stages: `ingest`, `classify`, `extract`, `thread`, `anchor`, `report`. `--dump state.json` writes
the intermediate state. `examples/` holds the output for the synthetic package.

## How it works

| Stage | Module | Model | What it does |
|---|---|---|---|
| Ingest | `ingest.py` | none | Reads PDFs, detects scanned pages, OCRs only those, records per-page OCR confidence |
| Classify | `classify.py` | Haiku | Types each page; a merged PDF is split into sections by content, minutes per meeting; low confidence is surfaced |
| Extract | `extract.py` | Sonnet | Typed facts with topic, status, date, amounts, verbatim quote and page. Facts that fail the anchor rule are discarded and logged |
| Thread | `threads.py` | none | Links mentions across meetings into issue threads, computes metrics, applies the config severity rules, exposure by unit entitlement |
| Judge | `judge.py` | Opus | Cross-checks each thread's severity and writes narratives. Disagreements are recorded and shown, never adopted. Dollar figures in narratives are checked against the facts |
| Report | `report.py` | none | Agent and client HTML from the prototype template, flags.json |

Configuration is data, not code: `config/taxonomy.yaml` (categories, fact kinds, severity rules,
staleness rule, wording), `config/models.yaml` (model routing, thresholds, prices), `config/tenant.yaml`
(brand). Swap the taxonomy for one derived from real outcomes without touching Python.

## Honesty rules, enforced in code

- Every fact must cite a page that was sent in that call and quote text found on it (`extract.validate_fact`).
- A flag cannot exist without citations (schema) and the template refuses to render one (`report.cites_or_refuse`).
- Absence is reported as absence: missing document types become `document_absent` facts; a recommendation with no recorded follow-up reads "completion not determinable from these documents".
- Exposure reads "Not determinable from these documents" when the cost range or unit entitlement is missing. No range is ever invented.
- Low-OCR pages, low-confidence sections, discarded facts and model disagreements are listed in the agent appendix.

## Web app

`strata-review-web` (or `uvicorn web.app:app --reload`). Upload, status (polls every two seconds), report
with agent and client tabs and downloads. Jobs run in a background thread and live under `web_jobs/`.
Single tenant from `config/tenant.yaml`; `web/tenants.py:get_tenant` is the seam for a tenant table. No auth yet.

## Deploy to a public URL (to link or embed from your own site)

Running locally only serves `127.0.0.1` on your own machine — nothing outside it can reach that
address. To get a URL you can link to, or put in an `<iframe>` on your own website, put it on a
host that keeps a server running. `Dockerfile` and `render.yaml` in this repo are set up for
[Render](https://render.com), which has a free tier and needs no server administration:

1. Push this repo to your own GitHub account (or use it directly from `zkassam23/strata-review`
   if Render can read it there).
2. In the Render dashboard: **New +** -> **Blueprint** -> pick the repo. Render reads
   `render.yaml` and builds the `Dockerfile` automatically — nothing to configure.
3. When it asks for `ANTHROPIC_API_KEY`, paste your key there. It is stored by Render, not in
   the repo.
4. Deploy. Render gives you a URL like `https://strata-review-xxxx.onrender.com` in a few minutes.
   That is the address to link to or embed.

The free tier sleeps after inactivity, so the first load after a quiet spell takes upwards of
30 seconds while it wakes up; every load after that is normal speed. Any other host that runs a
Dockerfile (Fly.io, Railway, a VPS) works the same way — the Dockerfile does not assume Render.

This puts the upload, status and report pages on the internet; it is not yet the small
embeddable lead-capture widget from the prototype (paste-your-email-and-attach-documents on a
listing page) — that is still on the open list along with PDF export, a completion email, and
auth.

## Tests

```bash
python -m pytest -q          # ~2 min, mostly OCR on the synthetic scans
```

Unit tests cover ingestion, classification, page anchoring, threading, the report template and the
web layer; `tests/test_e2e.py` runs the CLI on the synthetic package and checks for three reds and at
least two ambers. Tests never call the API.

## Cost

A 75-page package costs roughly $0.35 in model calls at list prices (Haiku classify, Sonnet extract,
one Opus judgment). Responses are cached on disk under `.cache/llm/`, so a re-run after a crash is free.
