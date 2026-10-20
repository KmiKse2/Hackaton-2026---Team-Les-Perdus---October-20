"""Strict output contract shared by Structured Outputs and the HTTP response."""
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*")
    @classmethod
    def plain_text(cls, value):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    cls.plain_text(item)
        if isinstance(value, str) and re.search(r"<[^>]+>|\b[a-z][a-z0-9+.-]*://|www\.|javascript:|data:|mailto:", value, re.I):
            raise ValueError("Only plain text without links is allowed")
        return value


class Need(AIModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    hypothesis_refs: list[str]
    signal_refs: list[str]
    requires_confirmation: bool


class Recommendation(AIModel):
    goal_id: int | None = None
    id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=90)
    message: str = Field(min_length=1, max_length=280)
    description: str = Field(min_length=1, max_length=1500)
    need_id: str
    category: str | None
    signal_refs: list[str]
    service_id: str | None
    justification: str = Field(min_length=1)
    requires_confirmation: bool
    clarification_question: str | None = Field(max_length=300)
    action_type: Literal["view_details"]
    action_label: Literal["View details"]
    display_priority: int = Field(ge=1, le=5)


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
    recommendations: list[Recommendation] = Field(max_length=5)
    clarification_questions: list[ClarificationQuestion]
    suggested_actions: list[SuggestedAction]


class AIAnalysis(AnalysisContent):
    # Bound locally to the authorised route, never supplied by the model.
    customer_id: int
