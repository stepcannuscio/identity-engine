"""Pydantic schemas for the identity-engine FastAPI server."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class ArtifactAnalysisStatus(str, Enum):
    NOT_ANALYZED = "not_analyzed"
    QUEUED = "queued"
    RUNNING = "running"
    ANALYZED = "analyzed"
    FALLBACK_ANALYZED = "fallback_analyzed"
    FAILED = "failed"


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


class QueryIntentMetadata(BaseModel):
    """Privacy-safe query-planning metadata for UI and feedback."""

    source_profile: Literal[
        "self_question",
        "artifact_grounded_self",
        "evidence_based",
        "preference_sensitive",
        "voice_generation",
        "general",
    ]
    intent_tags: list[str] = []
    domain_hints: list[str] = []


class PrivacyState(BaseModel):
    """Privacy-safe execution summary for frontend display."""

    execution_mode: Literal["local", "external", "blocked", "unknown"]
    routing_enforced: bool
    warning_present: bool
    used_local_fallback: bool = False
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
    intent: QueryIntentMetadata
    attributes_used: int
    backend_used: str
    requested_backend: str | None = None
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


class QueryFeedbackRequest(BaseModel):
    """Request body for local-only query usefulness feedback."""

    query: str
    response: str
    feedback: Literal["helpful", "ungrounded", "missed_context", "wrong_focus"]
    voice_feedback: Literal[
        "authentic",
        "not_me",
        "too_formal",
        "too_wordy",
        "wrong_rhythm",
        "overdone_style",
    ] | None = None
    notes: str | None = None
    query_type: str
    backend_used: Literal["local", "external"]
    confidence: Literal[
        "high_confidence",
        "medium_confidence",
        "low_confidence",
        "insufficient_data",
    ]
    intent: QueryIntentMetadata
    domains_referenced: list[str] = []


class QueryFeedbackResponse(BaseModel):
    """Response returned after storing query feedback."""

    id: str
    stored: bool = True


class RoutingLogEntry(BaseModel):
    """One routing decision made during a query session."""

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
    analysis_status: ArtifactAnalysisStatus = ArtifactAnalysisStatus.NOT_ANALYZED


class ArtifactAnalysisAttributeCandidate(BaseModel):
    """One reviewable artifact-derived attribute candidate."""

    candidate_id: str
    domain: str
    label: str
    value: str
    elaboration: str | None = None
    mutability: Literal["stable", "evolving"]
    confidence: float
    status: Literal["pending", "promoted"] = "pending"


class ArtifactAnalysisPreferenceCandidate(BaseModel):
    """One reviewable artifact-derived preference candidate."""

    candidate_id: str
    category: str
    subject: str
    signal: Literal["like", "dislike", "accept", "reject", "prefer", "avoid"]
    strength: int
    summary: str | None = None
    status: Literal["pending", "promoted"] = "pending"


class ArtifactAnalysisResponse(BaseModel):
    """Local-only artifact analysis payload."""

    artifact_id: str
    analysis_status: ArtifactAnalysisStatus
    analysis_method: Literal["model", "heuristic_fallback"] | None = None
    analysis_warning: str | None = None
    content_kind: str | None = None
    summary: str | None = None
    descriptor_tokens: list[str] = []
    candidate_attributes: list["ArtifactAnalysisAttributeCandidate"] = []
    candidate_preferences: list["ArtifactAnalysisPreferenceCandidate"] = []
    analyzed_at: datetime | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    can_retry: bool = False


class ArtifactPromoteRequest(BaseModel):
    """Promotion payload for accepted artifact-analysis candidates."""

    selected_attributes: list["ArtifactAnalysisAttributeCandidate"] = []
    selected_preferences: list["ArtifactAnalysisPreferenceCandidate"] = []


class ArtifactPromoteResponse(BaseModel):
    """Promotion result for one analyzed artifact."""

    artifact_id: str
    promoted_attribute_ids: list[str] = []
    promoted_preference_signal_ids: list[str] = []
    analysis: ArtifactAnalysisResponse


class ProviderStatusResponse(BaseModel):
    """One provider readiness summary."""

    provider: str
    label: str
    deployment: Literal["local", "external"]
    trust_boundary: Literal["self_hosted", "external"]
    auth_strategy: Literal["none", "api_key"]
    configured: bool
    available: bool
    validated: bool
    is_local: bool
    description: str | None = None
    setup_hint: str | None = None
    credential_fields: list["ProviderCredentialField"] = []
    model: str | None = None
    reason: str | None = None


class ProviderCredentialField(BaseModel):
    """One provider credential field rendered in onboarding."""

    name: str
    label: str
    input_type: Literal["password", "text"]
    placeholder: str | None = None
    secret: bool = True


class PrivacyPreferenceOption(BaseModel):
    """One privacy preference used to rank recommended configurations."""

    code: Literal["privacy_first", "balanced", "capability_first"]
    label: str
    description: str


class PrivacyProfileOption(BaseModel):
    """One selectable onboarding model/provider configuration."""

    code: Literal["private_local_first", "balanced_hybrid", "external_assist"]
    label: str
    description: str
    default_backend: Literal["local", "external"]
    provider_scope: Literal["self_hosted_only", "hybrid", "external_default"]
    provider_options: list[str] = []
    recommended_provider: str | None = None
    recommendation_reason: str
    requires_external_provider: bool
    available: bool
    recommended: bool


class SecurityCheckResponse(BaseModel):
    """One inspected machine-security recommendation."""

    code: str
    label: str
    status: Literal["enabled", "disabled", "unknown"]
    recommended_value: str = ""
    action_required: bool = False
    user_marked_complete: bool = False
    summary: str
    recommendation: str


class SecurityPostureResponse(BaseModel):
    """Machine security posture summary."""

    platform: str
    supported: bool
    checks: list[SecurityCheckResponse]


class SecurityCheckOverrideRequest(BaseModel):
    """Manual completion override for a machine-security recommendation."""

    completed: bool


class SetupOptionsResponse(BaseModel):
    """Current provider readiness and recommended profiles."""

    providers: list[ProviderStatusResponse]
    privacy_preference: Literal["privacy_first", "balanced", "capability_first"] | None = None
    privacy_preferences: list[PrivacyPreferenceOption]
    profiles: list[PrivacyProfileOption]
    active_profile: str | None
    preferred_provider: str | None = None
    preferred_backend: Literal["local", "external"]


class SetupProfileRequest(BaseModel):
    """Persisted onboarding profile selection."""

    profile: Literal["private_local_first", "balanced_hybrid", "external_assist"]
    privacy_preference: Literal["privacy_first", "balanced", "capability_first"] | None = None
    preferred_provider: str | None = None
    preferred_backend: Literal["local", "external"] | None = None
    onboarding_completed: bool | None = None


class ProviderCredentialRequest(BaseModel):
    """Credential payload for a supported external provider."""

    api_key: str | None = None
    credentials: dict[str, str] | None = None


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
    privacy_preference: Literal["privacy_first", "balanced", "capability_first"] | None = None
    privacy_preferences: list[PrivacyPreferenceOption]
    active_profile: str | None
    preferred_provider: str | None = None
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
    allow_external_extraction: bool = False


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
    allow_external_extraction: bool = False


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
    allow_external_extraction: bool = False


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
