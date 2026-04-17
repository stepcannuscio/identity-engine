"""Prompt assembly for grounded identity-aware responses.

This module builds chat-style messages from retrieved attributes and bounded
conversation history. It performs no model calls.
"""

from __future__ import annotations

from collections import defaultdict

from engine.context_assembler import AssembledContext


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
{preference_guidance}
{artifact_evidence}
"""

ARTIFACT_EXCERPT_MAX_CHARS = 700


def _visible_preference_attributes(
    preference_attributes: list[dict],
    target_backend: str,
) -> list[dict]:
    if target_backend == "local":
        return preference_attributes
    return [
        attribute
        for attribute in preference_attributes
        if attribute.get("routing") != "local_only"
    ]


def _assert_routing(
    attributes: list[dict],
    preference_attributes: list[dict],
    target_backend: str,
) -> None:
    if target_backend == "local":
        return

    visible_preferences = _visible_preference_attributes(preference_attributes, target_backend)
    violations = [
        attribute
        for attribute in attributes + visible_preferences
        if attribute.get("routing") == "local_only"
    ]
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


def _format_preference_guidance(context: AssembledContext, target_backend: str) -> str:
    summary = context.preference_summary or {}
    positive_items = list(summary["positive"])
    negative_items = list(summary["negative"])

    if target_backend != "local":
        positive_items = [
            item
            for item in positive_items
            if item.get("routing") != "local_only"
        ]
        negative_items = [
            item
            for item in negative_items
            if item.get("routing") != "local_only"
        ]

    if not positive_items and not negative_items:
        return ""

    lines = ["", "Learned preference guidance:"]
    for item in positive_items:
        summary_text = str(item.get("summary", ""))
        source = str(item.get("status") or item.get("source") or "preference")
        lines.append(f"- Prefer: {summary_text} [{source}]")
    for item in negative_items:
        summary_text = str(item.get("summary", ""))
        source = str(item.get("status") or item.get("source") or "preference")
        lines.append(f"- Avoid: {summary_text} [{source}]")
    return "\n".join(lines)


def _format_artifact_evidence(context: AssembledContext, target_backend: str) -> str:
    if not context.artifact_chunks:
        return ""
    if target_backend != "local":
        return ""

    lines = ["", "Relevant local artifact evidence:"]
    for chunk in context.artifact_chunks:
        title = str(chunk.get("title", "Untitled artifact"))
        excerpt = str(chunk.get("content", "")).strip()
        if len(excerpt) > ARTIFACT_EXCERPT_MAX_CHARS:
            excerpt = excerpt[: ARTIFACT_EXCERPT_MAX_CHARS - 3].rstrip() + "..."
        lines.append(f"- {title} [chunk {int(chunk.get('chunk_index', 0)) + 1}]: {excerpt}")
    return "\n".join(lines)


def build_prompt(
    context: AssembledContext,
    target_backend: str = "local",
    *,
    enforce_routing: bool = True,
) -> list[dict]:
    """Build the final message array for response generation.

    Args:
        context: Structured assembled context for the current task.
        target_backend: "local" for local inference, otherwise provider name.
        enforce_routing: Keep the prompt-builder fail-closed guard enabled.
    """
    _ = context.retrieval_mode  # reserved for future prompt variations

    if enforce_routing:
        _assert_routing(
            context.attributes,
            context.preference_attributes,
            target_backend,
        )

    formatted_attributes = _format_attributes(context.attributes)
    preference_guidance = _format_preference_guidance(context, target_backend)
    artifact_evidence = _format_artifact_evidence(context, target_backend)
    system_message = {
        "role": "system",
        "content": SYSTEM_PROMPT_TEMPLATE.format(
            formatted_attributes=formatted_attributes,
            preference_guidance=preference_guidance,
            artifact_evidence=artifact_evidence,
        ),
    }

    messages = [system_message]
    messages.extend(context.session_history)
    messages.append({"role": "user", "content": context.input_text})
    return messages
