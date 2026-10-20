"""Strict output contract shared by Structured Outputs and the HTTP response."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Need(AIModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    hypothesis_refs: list[str]
    signal_refs: list[str]
    requires_confirmation: bool


class Recommendation(AIModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    need_id: str
    signal_refs: list[str]
    service_id: str | None
    justification: str = Field(min_length=1)
    requires_confirmation: bool


class ClarificationQuestion(AIModel):
    question: str = Field(min_length=1)
    need_id: str | None


class SuggestedAction(AIModel):
    description: str = Field(min_length=1)
    recommendation_id: str
    execution: Literal["proposal_only"]


class AnalysisContent(AIModel):
    summary: str = Field(min_length=1)
    detected_needs: list[Need]
    recommendations: list[Recommendation]
    clarification_questions: list[ClarificationQuestion]
    suggested_actions: list[SuggestedAction]


class AIAnalysis(AnalysisContent):
    # Bound locally to the authorised route, never supplied by the model.
    customer_id: int
