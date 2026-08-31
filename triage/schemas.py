from typing import List, Optional

from pydantic import BaseModel, Field

from .config import COMPONENTS, SEVERITY_LEVELS


class TriageOutput(BaseModel):
    severity: str = Field(description=f"One of: {', '.join(SEVERITY_LEVELS)}")
    component: str = Field(description=f"One of: {', '.join(COMPONENTS)}")
    rationale: str = Field(description="One to two sentence justification.")


class Exemplar(BaseModel):
    correction_id: int
    report_title: str
    report_description: str
    original_severity: str
    original_component: str
    corrected_severity: str
    corrected_component: str
    corrected_rationale: str
    similarity: float


class BugReport(BaseModel):
    id: int
    title: str
    description: str
    split: str
    gold_severity: str
    gold_component: str


class PromptVersion(BaseModel):
    id: int
    version_number: int
    label: Optional[str]
    system_prompt: str
    correction_cutoff: str
    parent_id: Optional[int]
    is_active: bool
    created_at: str


class EvalResultRow(BaseModel):
    report_id: int
    predicted_severity: str
    predicted_component: str
    predicted_rationale: str
    severity_correct: bool
    component_correct: bool


class EvalRun(BaseModel):
    id: int
    prompt_version_id: int
    severity_accuracy: float
    component_accuracy: float
    joint_accuracy: float
    n_examples: int
    created_at: str
    results: List[EvalResultRow] = []
