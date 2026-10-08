"""Structured outputs produced by each agent. Routing decisions are made on these typed fields."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]

# --- Product Manager ------------------------------------------------------------------------------


class Ambiguity(BaseModel):
    question: str
    why_it_matters: str
    assumption: str = Field(description="What the team will assume if nobody answers")
    blocking: bool = Field(description="True only if a wrong assumption would invalidate the implementation")


class WorkItem(BaseModel):
    id: str = Field(description="Short id such as T1")
    title: str
    description: str
    owner: Literal["architect", "developer", "qa", "security", "deployment"]


class AcceptanceCriterion(BaseModel):
    id: str = Field(description="Short id such as AC1")
    description: str
    verification: Literal["automated_test", "code_review", "security_review", "manual"]


class RequirementsSpec(BaseModel):
    summary: str
    goals: list[str]
    work_items: list[WorkItem]
    acceptance_criteria: list[AcceptanceCriterion]
    ambiguities: list[Ambiguity]
    non_functional_requirements: list[str]
    out_of_scope: list[str]


# --- Architect ------------------------------------------------------------------------------------


class FileChangePlan(BaseModel):
    path: str
    action: Literal["create", "modify", "delete"]
    purpose: str


class ComponentDesign(BaseModel):
    name: str
    responsibility: str
    files: list[str]


class DesignRisk(BaseModel):
    risk: str
    severity: Literal["low", "medium", "high"]
    mitigation: str


class TechnicalDesign(BaseModel):
    overview: str
    detected_stack: list[str]
    approach: str
    components: list[ComponentDesign]
    affected_files: list[FileChangePlan]
    data_model_changes: list[str]
    api_changes: list[str]
    dependencies_to_add: list[str]
    risks: list[DesignRisk]
    test_strategy: str
    implementation_steps: list[str]
    revision_notes: str = Field(description="What changed relative to the previous design, or empty for v1")


# --- Developer ------------------------------------------------------------------------------------


class FeedbackResponse(BaseModel):
    message_id: str
    action: Literal["fixed", "disputed", "deferred"]
    rationale: str


class ImplementationSummary(BaseModel):
    summary: str
    files_changed: list[str]
    decisions: list[str] = Field(description="Important implementation decisions and why they were made")
    feedback_responses: list[FeedbackResponse]
    dependencies_added: list[str]
    notes_for_qa: str


# --- QA -------------------------------------------------------------------------------------------


class QAWork(BaseModel):
    tests_added_or_changed: list[str]
    test_plan: str
    criteria_covered: list[str] = Field(description="Acceptance criterion ids exercised by automated tests")


class FailureAnalysis(BaseModel):
    test: str
    root_cause: str
    suggested_fix: str
    file: str
    is_test_defect: bool = Field(description="True when the test itself is wrong rather than the implementation")


class AcceptanceVerdict(BaseModel):
    criterion_id: str
    status: Literal["verified", "failed", "not_verified"]
    evidence: str


class QAAnalysis(BaseModel):
    summary: str
    failures: list[FailureAnalysis]
    acceptance: list[AcceptanceVerdict]
    feedback_for_developer: list[str]
    test_quality_concerns: list[str]


# --- Security -------------------------------------------------------------------------------------


class SecurityFinding(BaseModel):
    id: str = Field(description="Short id such as S1")
    title: str
    severity: Severity
    category: Literal["authentication", "authorization", "input_validation", "injection", "secrets",
                      "cryptography", "session_management", "dependencies", "configuration", "logging", "other"]
    cwe: str
    file: str
    line: int
    description: str
    remediation: str


class ToolFindingAssessment(BaseModel):
    finding_id: str
    verdict: Literal["confirmed", "false_positive"]
    rationale: str


class DisputeResolution(BaseModel):
    message_id: str
    verdict: Literal["withdrawn", "maintained"]
    rationale: str


class SecurityReview(BaseModel):
    summary: str
    findings: list[SecurityFinding]
    tool_assessments: list[ToolFindingAssessment]
    dispute_resolutions: list[DisputeResolution]
    checked_areas: list[str]


# --- Code reviewer --------------------------------------------------------------------------------


class ReviewComment(BaseModel):
    file: str
    line: int
    severity: Literal["blocker", "major", "minor", "nit"]
    category: Literal["bug", "design", "duplication", "maintainability", "performance", "testing", "style", "docs"]
    body: str


class CodeReview(BaseModel):
    verdict: Literal["approve", "request_changes", "request_redesign"]
    summary: str
    comments: list[ReviewComment]
    architecture_consistency: str
    strengths: list[str]


# --- Deployment -----------------------------------------------------------------------------------


class EnvVar(BaseModel):
    name: str
    required: bool
    secret: bool
    description: str


class DeploymentReview(BaseModel):
    summary: str
    environment_variables: list[EnvVar]
    infrastructure_requirements: list[str]
    docker_issues: list[str]
    migration_steps: list[str]
    deployment_instructions: list[str]
    rollback_plan: str
    production_readiness_risks: list[str]
