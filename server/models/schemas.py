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


class QueryMetadata(BaseModel):
    """Metadata emitted for each query response."""

    query_type: str
    attributes_used: int
    backend_used: str
    domains_referenced: list[str]
    duration_ms: int
    privacy: PrivacyState


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
