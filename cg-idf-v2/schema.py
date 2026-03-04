"""
CG-IDF v2 — Schema Definitions
All Pydantic models used across the pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AnswerType(str, Enum):
    supported = "supported"        # backed by explicit evidence_refs
    inferred  = "inferred"         # reasonable inference, no direct ref
    unknown   = "unknown"          # cannot be determined from evidence


class VerificationStatus(str, Enum):
    confirm               = "confirm"
    downgrade             = "downgrade"
    contradiction         = "contradiction"
    insufficient_evidence = "insufficient_evidence"
    missing_evidence      = "missing_evidence"


class FlagCode(str, Enum):
    MISSING_SURFACE         = "MISSING_SURFACE"
    UNSUPPORTED_CLAIM       = "UNSUPPORTED_CLAIM"
    MISSING_ANSWER          = "MISSING_ANSWER"
    LOW_CONFIDENCE          = "LOW_CONFIDENCE"
    CONTRADICTION_DETECTED  = "CONTRADICTION_DETECTED"
    INCOMPLETE_COVERAGE     = "INCOMPLETE_COVERAGE"


# ---------------------------------------------------------------------------
# Evidence (human-supplied, Step 1)
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    evidence_id:     str
    surface:         str           # e.g. "home_feed", "checkout", "onboarding"
    platform:        str           # e.g. "ios", "android", "web"
    navigation_path: str           # e.g. "Home > Feed > Post Detail"
    uri:             str           # deep-link or URL captured
    # Raw description set by Provider A after image analysis
    raw_description: Optional[str] = None


# ---------------------------------------------------------------------------
# Screen Facts (extracted by Provider A per evidence item)
# ---------------------------------------------------------------------------

class ScreenFact(BaseModel):
    fact_id:     str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    evidence_id: str
    observation: str               # single atomic UI observation
    ui_element:  Optional[str] = None   # e.g. "CTA button", "badge", "modal"


# ---------------------------------------------------------------------------
# Questions & Answers (within a Layer)
# ---------------------------------------------------------------------------

class Question(BaseModel):
    q_id:          str             # e.g. "ENG_01", "MON_02"
    question_text: str
    llm_answer:    Optional[str]  = None
    answer_type:   Optional[AnswerType] = None
    confidence:    float          = 0.0   # 0.0 – 1.0
    evidence_refs: List[str]      = Field(default_factory=list)  # evidence_id list
    notes:         Optional[str]  = None

    @model_validator(mode="after")
    def supported_requires_refs(self) -> "Question":
        if self.answer_type == AnswerType.supported and not self.evidence_refs:
            raise ValueError(
                f"q_id={self.q_id}: answer_type='supported' requires at least one evidence_ref"
            )
        return self


# ---------------------------------------------------------------------------
# Layers  (analysis dimensions)
# ---------------------------------------------------------------------------

REQUIRED_LAYERS = [
    "engagement",
    "monetization",
    "retention",
    "social",
    "dark_patterns",
]

class Layer(BaseModel):
    layer_id:   str                        # matches REQUIRED_LAYERS
    label:      str
    sub_scores: Dict[str, float] = Field(default_factory=dict)
    questions:  List[Question]   = Field(default_factory=list)
    rollup_score: Optional[float] = None   # computed at merge step


# ---------------------------------------------------------------------------
# Rules Engine Outputs (Step 3)
# ---------------------------------------------------------------------------

class ReviewQueueItem(BaseModel):
    q_id:         str
    layer_id:     str
    reason:       str              # human-readable flag reason
    flag_code:    FlagCode
    ai1_answer:   Optional[str]   = None
    evidence_refs: List[str]      = Field(default_factory=list)
    screen_facts:  List[str]      = Field(default_factory=list)  # fact observation texts


# ---------------------------------------------------------------------------
# Provider B Verification Output (Step 4)
# ---------------------------------------------------------------------------

class VerificationResult(BaseModel):
    q_id:         str
    layer_id:     str
    status:       VerificationStatus
    rationale:    str
    revised_confidence: Optional[float] = None   # only when status == downgrade


# ---------------------------------------------------------------------------
# Merge / Final Report (Step 5)
# ---------------------------------------------------------------------------

class AuditFlag(BaseModel):
    flag_code:   FlagCode
    q_id:        Optional[str] = None
    layer_id:    Optional[str] = None
    description: str


class FinalReport(BaseModel):
    run_id:       str
    audit_id:     str = Field(default_factory=lambda: str(uuid.uuid4()))
    completed_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    layers:       Dict[str, Layer]
    flags:        List[AuditFlag]    = Field(default_factory=list)
    contradictions: List[str]        = Field(default_factory=list)
    overall_score:  Optional[float]  = None
    summary:        Optional[str]    = None


# ---------------------------------------------------------------------------
# LangGraph Pipeline State
# ---------------------------------------------------------------------------

class AuditState(BaseModel):
    """
    Shared mutable state passed between every LangGraph node.
    LangGraph requires the state to be reducible; we use a flat dict
    approach under the hood but expose it as a typed Pydantic model.
    """
    run_id:        str = Field(default_factory=lambda: str(uuid.uuid4()))
    evidence:      List[Evidence]              = Field(default_factory=list)
    screen_facts:  Dict[str, List[ScreenFact]] = Field(default_factory=dict)
    layers:        Dict[str, Layer]            = Field(default_factory=dict)
    review_queue:  List[ReviewQueueItem]       = Field(default_factory=list)
    verifications: List[VerificationResult]    = Field(default_factory=list)
    pipeline_flags: List[AuditFlag]            = Field(default_factory=list)
    final_report:  Optional[FinalReport]       = None
    errors:        List[str]                   = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")
