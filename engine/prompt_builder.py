"""Prompt assembly for grounded identity-aware responses.

This module builds chat-style messages from retrieved attributes and bounded
conversation history. It performs no model calls.
"""

from __future__ import annotations

from collections import defaultdict


class RoutingViolationError(Exception):
    """Raised when local-only attributes are included for an external backend."""


SYSTEM_PROMPT_TEMPLATE = """You are a personal identity assistant with access to a
structured model of who the user is. Your role is to give
direct, honest, concise answers grounded in what you know
about them. You do not speculate beyond what the identity
model contains. You do not give generic advice — every
response must be specifically grounded in the user's actual
attributes. If the identity model does not contain enough
information to answer well, say so briefly and explain what
additional context would help.

Respond concisely. No preamble. No \"based on your profile\"
or similar meta-commentary about using the identity model.
Just answer as if you simply know the person.

Identity model:
{formatted_attributes}
"""


def _assert_routing(attributes: list[dict], target_backend: str) -> None:
    if target_backend == "local":
        return

    violations = [a for a in attributes if a.get("routing") == "local_only"]
    if violations:
        labels = ", ".join(str(v.get("label", "unknown")) for v in violations)
        raise RoutingViolationError(
            "local_only attributes cannot be sent to external backends: "
            f"{labels}"
        )


def _format_attributes(attributes: list[dict]) -> str:
    if not attributes:
        return "(no relevant attributes retrieved)"

    by_domain: dict[str, list[dict]] = defaultdict(list)
    domain_top_scores: dict[str, float] = {}

    for attr in attributes:
        domain = str(attr.get("domain", "unknown"))
        by_domain[domain].append(attr)
        score = float(attr.get("score", 0.0))
        domain_top_scores[domain] = max(domain_top_scores.get(domain, 0.0), score)

    ordered_domains = sorted(
        domain_top_scores,
        key=lambda domain: domain_top_scores[domain],
        reverse=True,
    )
    lines: list[str] = []

    for domain in ordered_domains:
        domain_attrs = sorted(
            by_domain[domain],
            key=lambda a: float(a.get("score", 0.0)),
            reverse=True,
        )
        for attr in domain_attrs:
            label = str(attr.get("label", ""))
            value = str(attr.get("value", ""))
            lines.append(f"[{domain}] {label}: {value}")
            elaboration = attr.get("elaboration")
            if elaboration:
                lines.append(f"         {elaboration}")

    return "\n".join(lines)


def _cap_history(history: list[dict]) -> list[dict]:
    if not history:
        return []
    # Keep last 6 exchanges (12 messages).
    return history[-12:]


def build_prompt(
    query: str,
    attributes: list[dict],
    history: list[dict],
    query_type: str,
    target_backend: str = "local",
) -> list[dict]:
    """Build the final message array for response generation.

    Args:
        query: Current user query.
        attributes: Retrieved identity attributes.
        history: Prior user/assistant messages from the session.
        query_type: Query classification result (currently informational).
        target_backend: "local" for local inference, otherwise provider name.
    """
    _ = query_type  # reserved for future prompt variations

    _assert_routing(attributes, target_backend)

    formatted_attributes = _format_attributes(attributes)
    system_message = {
        "role": "system",
        "content": SYSTEM_PROMPT_TEMPLATE.format(
            formatted_attributes=formatted_attributes
        ),
    }

    messages = [system_message]
    messages.extend(_cap_history(history))
    messages.append({"role": "user", "content": query})
    return messages
