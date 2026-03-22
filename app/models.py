"""Pydantic models for API responses and internal data."""

from typing import Any

from pydantic import BaseModel, Field


class FileLOCEntry(BaseModel):
    path: str
    lines_of_code: int
    size_bytes: int


class FileLLMInsight(BaseModel):
    path: str
    summary: str
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class DimensionScores(BaseModel):
    security: float = Field(ge=0, le=100)
    code_quality: float = Field(ge=0, le=100)
    design: float = Field(ge=0, le=100)
    structure: float = Field(ge=0, le=100)
    naming: float = Field(ge=0, le=100)
    documentation: float = Field(ge=0, le=100)
    testing: float = Field(ge=0, le=100)
    performance: float = Field(ge=0, le=100)


class AnalyzeResponse(BaseModel):
    success: bool = True
    repository: str
    owner: str
    repo_name: str
    report_file: str
    overall_score: float = Field(ge=0, le=100)
    scores: DimensionScores
    weights_applied: dict[str, float]
    file_summary: list[FileLOCEntry]
    issues_found: list[str]
    suggestions: list[str] = Field(default_factory=list)
    architecture_feedback: str = ""
    documentation_review: str = ""
    design_summary: str = ""
    file_insights: list[FileLLMInsight]
    repo_level_insights: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    code: str | None = None
    detail: str | None = None
