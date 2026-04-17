"""Application-level inference broker.

The broker is the only application-layer path that decides whether an inference
task may proceed. It keeps privacy/routing checks close to the router boundary
while delegating all actual model calls to ``config.llm_router``.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Generic, TypeVar

from config.llm_router import ProviderConfig, extract_attributes, generate_response
from engine.prompt_builder import RoutingViolationError

T = TypeVar("T")


@dataclass(frozen=True)
class InferenceDecision:
    """Privacy-safe audit metadata describing one brokered inference decision."""

    provider: str | None
    model: str
    is_local: bool
    task_type: str
    blocked_external_attributes_count: int
    routing_enforced: bool
    attribute_count: int = 0
    domains_used: list[str] = field(default_factory=list)
    retrieval_mode: str | None = None
    contains_local_only_context: bool = False
    decision: str = "allowed"
    warning: str | None = None
    reason: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_routing_log_entry(
        self,
        *,
        query: str,
        query_type: str | None = None,
    ) -> dict[str, object]:
        """Serialize the decision into a session-safe routing-log entry."""
        resolved_query_type = query_type or self.retrieval_mode or ""
        return {
            "query": query,
            "query_type": resolved_query_type,
            "backend": "local" if self.is_local else (self.provider or "external"),
            "attribute_count": self.attribute_count,
            "domains_referenced": sorted(set(self.domains_used)),
            "task_type": self.task_type,
            "provider": self.provider,
            "model": self.model,
            "is_local": self.is_local,
            "routing_enforced": self.routing_enforced,
            "contains_local_only_context": self.contains_local_only_context,
            "blocked_external_attributes_count": self.blocked_external_attributes_count,
            "retrieval_mode": self.retrieval_mode,
            "decision": self.decision,
            "warning": self.warning,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class BrokeredResult(Generic[T]):
    """Inference result plus metadata for future audit logging."""

    content: T
    metadata: InferenceDecision


class AuditedRoutingViolationError(RoutingViolationError):
    """Routing violation that carries a structured audit decision."""

    def __init__(self, message: str, audit: InferenceDecision):
        super().__init__(message)
        self.audit = audit


class PrivacyBroker:
    """Central application-level inference boundary."""

    def __init__(self, provider_config: ProviderConfig):
        self.provider_config = provider_config

    def generate_grounded_response(
        self,
        messages: list[dict],
        *,
        attributes: list[dict],
        stream: bool = False,
        task_type: str = "query_generation",
        retrieval_mode: str | None = None,
    ) -> BrokeredResult[str | Generator[str, None, None]]:
        """Generate a grounded response after enforcing query routing rules."""
        decision = self._decide(
            task_type=task_type,
            attributes=attributes,
            enforce_routing=True,
            retrieval_mode=retrieval_mode,
        )
        response = generate_response(messages, self.provider_config, stream=stream)
        return BrokeredResult(content=response, metadata=decision)

    def extract_structured_attributes(
        self,
        messages: list[dict],
        *,
        task_type: str = "capture_extraction",
    ) -> BrokeredResult[str]:
        """Run structured extraction through the router.

        Capture currently preserves existing behavior: it sends the raw capture
        text to the resolved backend, then writes accepted attributes back as
        ``local_only``. This broker method is where stricter external capture
        policy can be added later without changing callers.
        """
        decision = self._decide(
            task_type=task_type,
            attributes=None,
            enforce_routing=False,
            retrieval_mode=None,
        )
        response = generate_response(messages, self.provider_config)
        assert isinstance(response, str)
        return BrokeredResult(content=response, metadata=decision)

    def extract_interview_attributes(
        self,
        question: str,
        answer: str,
        *,
        task_type: str = "interview_extraction",
    ) -> BrokeredResult[list[dict]]:
        """Run question/answer extraction for the guided interview flow.

        This preserves the current interview behavior while moving the
        application-level inference seam out of the script and into the broker.
        """
        decision = self._decide(
            task_type=task_type,
            attributes=None,
            enforce_routing=False,
            retrieval_mode=None,
        )
        extracted = extract_attributes(question, answer, self.provider_config)
        return BrokeredResult(content=extracted, metadata=decision)

    def _decide(
        self,
        *,
        task_type: str,
        attributes: list[dict] | None,
        enforce_routing: bool,
        retrieval_mode: str | None,
    ) -> InferenceDecision:
        attributes = attributes or []
        blocked_count = 0
        contains_local_only = any(
            attribute.get("routing") == "local_only" for attribute in attributes
        )
        domains_used = sorted(
            {
                str(attribute.get("domain", ""))
                for attribute in attributes
                if attribute.get("domain")
            }
        )

        decision = InferenceDecision(
            provider=self.provider_config.provider,
            model=self.provider_config.model,
            is_local=self.provider_config.is_local,
            task_type=task_type,
            blocked_external_attributes_count=0,
            routing_enforced=enforce_routing,
            attribute_count=len(attributes),
            domains_used=domains_used,
            retrieval_mode=retrieval_mode,
            contains_local_only_context=contains_local_only,
        )

        if enforce_routing and not self.provider_config.is_local:
            blocked = [
                attribute
                for attribute in attributes
                if attribute.get("routing") == "local_only"
            ]
            blocked_count = len(blocked)
            if blocked:
                labels = ", ".join(str(attr.get("label", "unknown")) for attr in blocked)
                blocked_decision = InferenceDecision(
                    provider=decision.provider,
                    model=decision.model,
                    is_local=decision.is_local,
                    task_type=decision.task_type,
                    blocked_external_attributes_count=blocked_count,
                    routing_enforced=decision.routing_enforced,
                    attribute_count=decision.attribute_count,
                    domains_used=decision.domains_used,
                    retrieval_mode=decision.retrieval_mode,
                    contains_local_only_context=decision.contains_local_only_context,
                    decision="blocked",
                    reason="local_only_context_blocked_for_external_inference",
                    warning="local_only attributes cannot be sent to external backends",
                )
                raise AuditedRoutingViolationError(
                    "local_only attributes cannot be sent to external backends: "
                    f"{labels}",
                    audit=blocked_decision,
                )

        return decision
