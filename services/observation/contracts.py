"""Validated wire contracts. Bytes are UTF-8 offsets; line numbers are one-based."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')

class SourceRef(Contract):
    project_id: str
    epoch: str|None = None
    kind: Literal['project', 'node', 'run', 'paper', 'file']
    object_id: str
    revision: int|None = None
    scope_id: str|None = None
    path: str|None = None
    file_generation: int|None = None
    segment_id: str|None = None
    attempt_id: str|None = None

class ProgressFact(Contract):
    id: str
    section: Literal['now', 'attention', 'blocked', 'recent', 'next', 'evidence']
    label: str
    value: str
    classification: Literal['OPERATIONAL', 'REPORTED'] = 'OPERATIONAL'
    applicability: Literal['current', 'historical', 'not_checked'] = 'current'
    observed_at: str
    sources: list[SourceRef]

class ObserverHealth(Contract):
    state: Literal['healthy', 'rebuilding', 'stale', 'unavailable']
    observed_at: str|None = None
    age_seconds: float|None = None
    database_coverage: str
    file_coverage: str
    history_gap: bool = False
    note: str

class ProjectProgressSnapshot(Contract):
    project_id: str
    epoch: str
    generation: int
    snapshot_id: str
    cursor: int
    project_revision: int
    observed_at: str
    controller_status: str
    facts: list[ProgressFact]
    run_counts: dict[str, int]
    health: ObserverHealth
    dependencies: dict[str, str|int]

class SourceSegment(Contract):
    id: str
    name: str
    kind: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    parser: str
    certainty: Literal['structural', 'raw'] = 'structural'

class ScopeCoverage(Contract):
    id: str
    kind: str
    object_id: str
    attempt_id: str|None
    scan_generation: int
    coverage: str
    observed_at: str|None
    error: str|None
    total: int

class ScopePage(Contract):
    items: list[ScopeCoverage]
    next_cursor: str|None

class FileObservation(Contract):
    id: str
    scope_id: str
    path: str
    generation: int
    state: str
    size: int
    parse_state: str
    attribution: str
    observed_at: str|None
    source: SourceRef

class FilePage(Contract):
    items: list[FileObservation]
    next_cursor: str|None
    coverage: ScopeCoverage

class SourceView(Contract):
    source: SourceRef
    availability: Literal['available', 'changed', 'deleted', 'unavailable']
    content: str|None = None
    segments: list[SourceSegment] = Field(default_factory=list)
    parse_state: str
    observed_at: str|None = None
    note: str
    project_path: str|None = None
    metadata: dict = Field(default_factory=dict)
    artifact_url: str|None = None

class ReporterSettings(Contract):
    enabled: bool = False
    automatic: bool = False
    provider_id: str|None = None
    cap_usd: float = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    max_requests: int = Field(default=20, ge=1, le=1000)
    max_output_tokens: int = Field(default=1024, ge=128, le=4096)
    language: Literal['en', 'zh'] = 'en'
    minimum_seconds: int = Field(default=60, ge=30, le=3600)
    maximum_wait_seconds: int = Field(default=180, ge=60, le=3600)

    @model_validator(mode='after')
    def spacing_within_wait(self):
        if self.minimum_seconds>self.maximum_wait_seconds:
            raise ValueError('Minimum spacing must not exceed maximum narrative wait')
        return self

class ReporterSettingsView(Contract):
    settings: ReporterSettings
    version: int
    estimated_usd: float
    reserved_usd: float
    requests: int
    unpriced_requests: int

class ReporterSettingsWrite(Contract):
    settings: ReporterSettings
    expected_version: int = Field(ge=0)

class ReporterRefresh(Contract):
    request_id: str = Field(min_length=1, max_length=160)

class ReporterReport(Contract):
    snapshot_id: str
    focus: Literal['activity', 'attention', 'evidence', 'no_material_change']
    fact_ids: list[str] = Field(max_length=12)

class ReporterJob(Contract):
    id: str
    status: str
    created_at: str
    updated_at: str
    error: str|None
    report: ReporterReport|None
    facts: list[ProgressFact]
    current: bool
    snapshot_id: str

class ReportPage(Contract):
    items: list[ReporterJob]
    next_cursor: str|None
