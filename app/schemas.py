from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

class NodeSummaryResponse(BaseModel):
    logical_node_id: str
    node_version_id: str
    version_number: int
    section_number: Optional[str]
    heading: str
    level: int
    content_hash: str
    change_status: str

class NodeDetailResponse(NodeSummaryResponse):
    parent_logical_id: Optional[str]
    canonical_path: str
    body_text: str
    tables_markdown: Optional[str] = None
    figures_captions: Optional[str] = None
    children: List[NodeSummaryResponse] = []

class NodeDiffResponse(BaseModel):
    logical_node_id: str
    canonical_path: str
    v1_version: int
    v2_version: int
    changed: bool
    change_status: str
    v1_hash: Optional[str]
    v2_hash: Optional[str]
    diff_summary: str
    numeric_threshold_changed: bool = Field(default=False)

class SelectionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    version_number: int = Field(..., ge=1)
    logical_node_ids: List[str] = Field(..., min_length=1)

class QATestCase(BaseModel):
    test_id: str = Field(...)
    title: str = Field(..., min_length=5)
    source_logical_node_id: str = Field(...)
    preconditions: List[str] = Field(..., min_length=1)
    steps: List[str] = Field(..., min_length=1)
    expected_result: str = Field(..., min_length=10)
    priority: str = Field(..., pattern="^(HIGH|MEDIUM|LOW)$")

    @field_validator("expected_result")
    @classmethod
    def check_concrete_expected_result(cls, v: str) -> str:
        if any(vague in v.lower() for vague in ["works properly", "behaves as expected", "functions normally"]):
            raise ValueError("expected_result must be concrete and verifiable, not vague.")
        return v

class LLMTestSuiteOutput(BaseModel):
    test_cases: List[QATestCase] = Field(..., min_length=3, max_length=5)

class GenerateRequest(BaseModel):
    force_regenerate: bool = Field(default=False)
