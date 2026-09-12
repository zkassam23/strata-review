"""Data model shared by every pipeline stage.

Every extracted fact carries at least one Citation with a source_doc and page_number.
Facts without one are discarded by extract.py and logged; they never reach a report.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

DocType = Literal[
    "council_minutes", "agm_sgm_minutes", "depreciation_report", "form_b",
    "financial_statements", "insurance_summary", "engineering_report", "bylaws", "other",
]
Severity = Literal["red", "amber", "note"]


class Page(BaseModel):
    doc_id: str                      # id of the SourceDoc this page belongs to
    source_file: str                 # filename as supplied (or zip member name)
    page_number: int                 # 1-based within the source file
    text: str = ""
    is_scanned: bool = False
    ocr_confidence: Optional[float] = None   # tesseract mean word confidence 0-100
    low_ocr: bool = False


class SourceDoc(BaseModel):
    doc_id: str
    source_file: str
    path: str
    n_pages: int
    pages: list[Page] = Field(default_factory=list)


class PageClassification(BaseModel):
    doc_id: str
    page_number: int
    doc_type: DocType
    confidence: float
    meeting_date: Optional[str] = None   # ISO date when the page is part of a dated meeting
    title: Optional[str] = None
    smoothed_from: Optional[str] = None  # original label if neighbour smoothing changed it


class Section(BaseModel):
    """A run of pages of one document type (and, for minutes, one meeting)."""
    section_id: str
    doc_id: str
    source_file: str
    doc_type: DocType
    page_start: int
    page_end: int
    confidence: float                # mean page confidence
    low_confidence: bool = False
    meeting_date: Optional[str] = None
    title: Optional[str] = None

    @property
    def page_numbers(self) -> list[int]:
        return list(range(self.page_start, self.page_end + 1))


class Citation(BaseModel):
    source_doc: str                  # source filename
    page_number: Optional[int] = None  # None only for kind == "absence"
    label: str                       # rendered text, e.g. "Council minutes, 14 Apr 2026, p. 3"
    kind: Literal["page", "absence"] = "page"


class Fact(BaseModel):
    fact_id: str
    category: str                    # taxonomy category key
    kind: str                        # taxonomy fact_kind
    summary: str
    section_id: str
    doc_type: DocType
    citations: list[Citation]
    date: Optional[str] = None       # meeting date / report date / statement date (ISO)
    topic: Optional[str] = None      # free-text topic used for threading, e.g. "roof replacement"
    status: Optional[str] = None     # discussed | quote_obtained | deferred | proposed | approved | completed | tendered | ongoing | none
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    data: dict[str, Any] = Field(default_factory=dict)  # kind-specific extras


class DiscardedFact(BaseModel):
    section_id: str
    reason: str
    payload: dict[str, Any]


class Thread(BaseModel):
    thread_id: str
    category: str
    topic: str
    fact_ids: list[str]
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    latest_status: Optional[str] = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    rule_severity: Optional[Severity] = None
    rule_criteria: Optional[str] = None


class Flag(BaseModel):
    flag_id: str
    category: str
    thread_id: Optional[str]
    severity: Severity
    title: str
    agent_text: str
    client_title: str
    client_text: str
    exposure: str                    # agent-facing exposure line
    client_exposure: str = ""        # plain-language exposure line (may be empty)
    citations: list[Citation]
    client_citations: list[Citation] = Field(default_factory=list)
    rationale: str = ""
    rule_severity: Optional[Severity] = None
    judge_disagreed: bool = False


class CategoryStatus(BaseModel):
    """One summary row per taxonomy category."""
    category: str
    label: str
    state: Literal["flagged", "clear", "absent"]
    severity: Optional[Severity] = None
    absence_text: Optional[str] = None


class Usage(BaseModel):
    model: str
    task: str
    input_tokens: int
    output_tokens: int
    mocked: bool = False


class PackageSummary(BaseModel):
    n_docs: int
    n_pages: int
    n_scanned: int
    n_low_ocr: int
    files: list[str]


class ReviewResult(BaseModel):
    building: dict[str, Any] = Field(default_factory=dict)   # address, strata_plan, city, year_built, construction
    unit: Optional[str] = None
    package: PackageSummary
    sections: list[Section]
    facts: list[Fact]
    discarded: list[DiscardedFact]
    threads: list[Thread]
    flags: list[Flag]
    category_status: list[CategoryStatus]
    questions: list[str] = Field(default_factory=list)
    low_ocr_pages: list[Page] = Field(default_factory=list)
    low_confidence_sections: list[Section] = Field(default_factory=list)
    usage: list[Usage] = Field(default_factory=list)
    generated_on: str = Field(default_factory=lambda: date.today().isoformat())
    overall_risk: str = "Not assessed"
    exposure_total: str = "Not determinable from these documents"
