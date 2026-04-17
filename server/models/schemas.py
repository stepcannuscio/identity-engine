"""Pydantic schemas for the identity-engine FastAPI server."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """Passphrase-based login request."""

    passphrase: str


class LoginResponse(BaseModel):
    """Successful login response."""

    token: str
    expires_at: datetime


class AuthStatus(BaseModel):
    """Authentication status for the current request."""

    authenticated: bool
    expires_at: datetime | None


class QueryRequest(BaseModel):
    """Request body for query endpoints."""

    query: str
    backend_override: str | None = None


class PrivacyState(BaseModel):
    """Privacy-safe execution summary for frontend display."""

    execution_mode: Literal["local", "external", "blocked", "unknown"]
    routing_enforced: bool
    warning_present: bool
    provider_label: str | None = None
    model_label: str | None = None
    summary: str


class CoverageCounts(BaseModel):
    """Counts of each signal type used in a coverage assessment."""

    attributes: int
    preferences: int
    artifacts: int


class AcquisitionGap(BaseModel):
    """One missing context area identified after coverage evaluation."""

    kind: Literal["identity", "preference", "artifact"]
    domain: str | None = None
    reason: str


class AcquisitionSuggestion(BaseModel):
    """One suggested next-best acquisition action."""

    kind: Literal["quick_capture", "interview_question", "artifact_upload"]
    prompt: str
    action: dict[str, str | int | float | bool | None]


class AcquisitionPlan(BaseModel):
    """Structured acquisition suggestions returned with query metadata."""

    status: Literal["not_needed", "suggested"]
    gaps: list[AcquisitionGap] = []
    suggestions: list[AcquisitionSuggestion] = []


class QueryMetadata(BaseModel):
    """Metadata emitted for each query response."""

    query_type: str
    attributes_used: int
    backend_used: str
    domains_referenced: list[str]
    duration_ms: int
    privacy: PrivacyState
    confidence: Literal[
        "high_confidence",
        "medium_confidence",
        "low_confidence",
        "insufficient_data",
    ]
    coverage: CoverageCounts
    coverage_notes: str | None = None
    acquisition: AcquisitionPlan


class RoutingLogEntry(BaseModel):
    """One routing decision made during a query session."""

    query: str
    query_type: str
    backend: str
    attribute_count: int
    domains_referenced: list[str] = []
    timestamp: datetime
    task_type: str | None = None
    provider: str | None = None
    model: str | None = None
    is_local: bool | None = None
    routing_enforced: bool | None = None
    contains_local_only_context: bool | None = None
    blocked_external_attributes_count: int = 0
    retrieval_mode: str | None = None
    decision: str | None = None
    warning: str | None = None
    reason: str | None = None
    privacy: PrivacyState | None = None


class QueryResponse(BaseModel):
    """Non-streaming query response."""

    response: str
    metadata: QueryMetadata


class ArtifactIngestResponse(BaseModel):
    """Summary returned after artifact ingestion."""

    artifact_id: str
    chunk_count: int
    tags: list[str] = []


class ProviderStatusResponse(BaseModel):
    """One provider readiness summary."""

    provider: Literal["ollama", "anthropic", "groq"]
    label: str
    configured: bool
    available: bool
    validated: bool
    is_local: bool
    model: str | None = None
    reason: str | None = None


class PrivacyProfileOption(BaseModel):
    """One selectable onboarding privacy/model profile."""

    code: Literal["private_local_first", "balanced_hybrid", "external_assist"]
    label: str
    description: str
    default_backend: Literal["local", "external"]
    requires_external_provider: bool
    available: bool
    recommended: bool


class SecurityCheckResponse(BaseModel):
    """One inspected machine-security recommendation."""

    code: str
    label: str
    status: Literal["enabled", "disabled", "unknown"]
    summary: str
    recommendation: str


class SecurityPostureResponse(BaseModel):
    """Machine security posture summary."""

    platform: str
    supported: bool
    checks: list[SecurityCheckResponse]


class SetupOptionsResponse(BaseModel):
    """Current provider readiness and recommended profiles."""

    providers: list[ProviderStatusResponse]
    profiles: list[PrivacyProfileOption]
    active_profile: str | None
    preferred_backend: Literal["local", "external"]


class SetupProfileRequest(BaseModel):
    """Persisted onboarding profile selection."""

    profile: Literal["private_local_first", "balanced_hybrid", "external_assist"]
    preferred_backend: Literal["local", "external"] | None = None
    onboarding_completed: bool | None = None


class ProviderCredentialRequest(BaseModel):
    """Credential payload for a supported external provider."""

    api_key: str


class TeachQuestionResponse(BaseModel):
    """One teach-question card."""

    id: str
    prompt: str
    domain: str | None
    intent_key: str
    source: Literal["catalog", "generated"]
    status: str
    priority: float


class TeachCard(BaseModel):
    """One Teach/onboarding card returned to the frontend."""

    type: Literal["welcome", "privacy_setup", "security_setup", "question"]
    title: str
    body: str
    payload: dict[str, object] = {}


class TeachBootstrapResponse(BaseModel):
    """Combined onboarding/bootstrap payload for the Teach tab."""

    onboarding_completed: bool
    active_profile: str | None
    preferred_backend: Literal["local", "external"]
    providers: list[ProviderStatusResponse]
    profiles: list[PrivacyProfileOption]
    security_posture: SecurityPostureResponse
    cards: list[TeachCard]
    questions: list[TeachQuestionResponse]


class TeachQuestionsResponse(BaseModel):
    """List of planned Teach questions."""

    questions: list[TeachQuestionResponse]


class TeachQuestionAnswerRequest(BaseModel):
    """Answer payload for a Teach question."""

    answer: str
    accepted: list["CapturePreviewWriteItem"] | None = None


class TeachQuestionFeedbackRequest(BaseModel):
    """Feedback payload for a Teach question."""

    feedback: Literal["skip", "not_relevant", "duplicate", "already_covered", "too_personal"]


class AttributeResponse(BaseModel):
    """Serialized identity attribute."""

    id: str
    domain: str
    label: str
    value: str
    elaboration: str | None
    mutability: str
    source: str
    confidence: float
    routing: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_confirmed: datetime | None


class PreferenceSignalCreateRequest(BaseModel):
    """Request body for creating one preference signal."""

    category: str
    subject: str
    signal: Literal["like", "dislike", "accept", "reject", "prefer", "avoid"]
    strength: int = 3
    source: Literal[
        "explicit_feedback",
        "behavior",
        "correction",
        "system_inference",
    ] = "explicit_feedback"
    context: dict[str, str | int | float | bool] | None = None
    attribute_id: str | None = None


class PreferenceSignalResponse(BaseModel):
    """Serialized stored preference signal."""

    id: str
    category: str
    subject: str
    signal: str
    strength: int
    source: str
    context: dict[str, str | int | float | bool] | None
    attribute_id: str | None
    created_at: datetime


class PreferenceSignalSummaryResponse(BaseModel):
    """Simple aggregated preference summary for one subject."""

    category: str
    subject: str
    observations: int
    positive_count: int
    negative_count: int
    net_score: int
    latest_at: datetime


class PreferencePromotionResponse(BaseModel):
    """Outcome for one manual preference-promotion pass."""

    category: str
    subject: str
    state: str
    action: str
    reason: str
    domain: str
    label: str
    attribute_id: str | None
    confidence: float | None
    observations: int
    positive_count: int
    negative_count: int
    net_score: int


class ProvenanceEvidenceSummary(BaseModel):
    """Privacy-safe summary of one evidence record."""

    source_type: str
    summary: str
    weight: float | None


class AttributeProvenanceResponse(BaseModel):
    """Attribute metadata plus privacy-safe provenance summaries."""

    attribute_id: str
    label: str
    source: str
    evidence: list[ProvenanceEvidenceSummary] = []


class AttributeUpdateRequest(BaseModel):
    """Updatable attribute fields."""

    value: str | None = None
    elaboration: str | None = None
    confidence: float | None = None
    routing: str | None = None
    mutability: str | None = None


class AttributeCorrectionRequest(BaseModel):
    """Action-based attribute correction payload."""

    action: Literal["confirm", "reject", "refine"]
    new_value: str | None = None
    elaboration: str | None = None
    confidence: float | None = None
    routing: str | None = None
    mutability: str | None = None


class CreateAttributeRequest(BaseModel):
    """Request body for creating a new attribute."""

    domain: str
    label: str
    value: str
    elaboration: str | None = None
    mutability: str
    source: str
    confidence: float
    routing: str = "local_only"


class CaptureRequest(BaseModel):
    """Request body for quick-capture endpoints."""

    text: str
    domain_hint: str | None = None
    accepted: list["CapturePreviewWriteItem"] | None = None


class CapturePreviewItem(BaseModel):
    """One extracted attribute proposed by capture preview."""

    domain: str
    label: str
    value: str
    elaboration: str | None
    mutability: str
    confidence: float
    conflicts_with: AttributeResponse | None


class CapturePreviewWriteItem(BaseModel):
    """One accepted preview item submitted for persistence."""

    domain: str
    label: str
    value: str
    elaboration: str | None
    mutability: str
    confidence: float


class CapturePreviewResponse(BaseModel):
    """Preview response containing extracted attributes not yet written."""

    proposed: list[CapturePreviewItem]


class CaptureResponse(BaseModel):
    """Quick-capture write response."""

    attributes_saved: int
    attributes: list[AttributeResponse]


class InterviewPreviewRequest(BaseModel):
    """Request body for interview preview/save endpoints."""

    domain: str
    question: str
    answer: str
    accepted: list["CapturePreviewWriteItem"] | None = None


class InterviewPreviewResponse(BaseModel):
    """Preview response for one guided interview answer."""

    proposed: list[CapturePreviewItem]


class InterviewResponse(BaseModel):
    """Saved interview attributes."""

    attributes_saved: int
    attributes: list[AttributeResponse]


class SessionRecord(BaseModel):
    """Stored reflection-session summary."""

    id: str
    session_type: str
    summary: str | None
    attributes_created: int
    attributes_updated: int
    external_calls_made: int
    started_at: datetime
    ended_at: datetime | None
    routing_log: list[RoutingLogEntry] = []
    privacy: PrivacyState | None = None


class CurrentSessionStatus(BaseModel):
    """Live in-memory query session summary."""

    id: str
    query_count: int
    attributes_retrieved: int
    backend: str
    started_at: datetime


class DomainSummary(BaseModel):
    """Domain count response."""

    domain: str
    attribute_count: int
