"""Application-level inference broker.

The broker is the only application-layer path that decides whether an inference
task may proceed. It keeps privacy/routing checks close to the router boundary
while delegating all actual model calls to ``config.llm_router``.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Generic, TypeVar

from config.llm_router import ProviderConfig, extract_attributes, generate_response
from engine.prompt_builder import RoutingViolationError

T = TypeVar("T")


@dataclass(frozen=True)
class InferenceDecision:
    """Minimal metadata describing an approved inference call."""

    provider: str | None
    model: str
    is_local: bool
    task_type: str
    blocked_external_attributes_count: int
    routing_enforced: bool


@dataclass(frozen=True)
class BrokeredResult(Generic[T]):
    """Inference result plus metadata for future audit logging."""

    content: T
    metadata: InferenceDecision


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
    ) -> BrokeredResult[str | Generator[str, None, None]]:
        """Generate a grounded response after enforcing query routing rules."""
        decision = self._decide(
            task_type=task_type,
            attributes=attributes,
            enforce_routing=True,
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
        )
        extracted = extract_attributes(question, answer, self.provider_config)
        return BrokeredResult(content=extracted, metadata=decision)

    def _decide(
        self,
        *,
        task_type: str,
        attributes: list[dict] | None,
        enforce_routing: bool,
    ) -> InferenceDecision:
        blocked_count = 0

        if enforce_routing and not self.provider_config.is_local:
            blocked = [
                attribute
                for attribute in attributes or []
                if attribute.get("routing") == "local_only"
            ]
            blocked_count = len(blocked)
            if blocked:
                labels = ", ".join(str(attr.get("label", "unknown")) for attr in blocked)
                raise RoutingViolationError(
                    "local_only attributes cannot be sent to external backends: "
                    f"{labels}"
                )

        return InferenceDecision(
            provider=self.provider_config.provider,
            model=self.provider_config.model,
            is_local=self.provider_config.is_local,
            task_type=task_type,
            blocked_external_attributes_count=blocked_count,
            routing_enforced=enforce_routing,
        )
