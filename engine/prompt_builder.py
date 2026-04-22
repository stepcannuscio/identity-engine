"""Prompt assembly for grounded identity-aware responses.

This module builds chat-style messages from retrieved attributes and bounded
conversation history. It performs no model calls.
"""

from __future__ import annotations

from engine.context_assembler import AssembledContext, EvidenceItem


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

Structured identity remains the canonical user model.
Artifacts are supporting evidence only and should ground or
corroborate answers rather than replace identity facts.
If you are asked to draft or rewrite in the user's voice,
imitate only the grounded traits below. Keep the voice
subtle and natural. Do not invent signature phrases,
biographical details, or exaggerated quirks.

Grounded context:
{grounded_context}
{voice_guidance}
{artifact_guidance}
{confidence_guidance}
"""

ARTIFACT_EXCERPT_MAX_CHARS = 350

_MEDIUM_CONFIDENCE_HINT = (
    "Coverage note: grounded context is partial. Answer what you can and briefly "
    "acknowledge the gap rather than extrapolating."
)

_LOW_CONFIDENCE_HINT = (
    "Coverage note: very little grounded context is available. Be cautious, "
    "state what you don't know, and suggest what additional context would help."
)


def _visible_preference_attributes(
    preference_attributes: list[dict],
    target_backend: str,
) -> list[dict]:
    if target_backend in ("local", "private_server"):
        return preference_attributes
    return [
        attribute
        for attribute in preference_attributes
        if attribute.get("routing") != "local_only"
    ]


def _visible_artifact_chunks(
    artifact_chunks: list[dict],
    target_backend: str,
) -> list[dict]:
    if target_backend in ("local", "private_server"):
        return artifact_chunks
    return [chunk for chunk in artifact_chunks if chunk.get("routing") != "local_only"]


def _assert_routing(
    attributes: list[dict],
    preference_attributes: list[dict],
    artifact_chunks: list[dict],
    target_backend: str,
) -> None:
    if target_backend in ("local", "private_server"):
        return

    violations = [
        attribute
        for attribute in attributes + preference_attributes + artifact_chunks
        if attribute.get("routing") == "local_only"
    ]
    if violations:
        labels = ", ".join(
            str(v.get("label", v.get("title", "unknown")))
            for v in violations
        )
        raise RoutingViolationError(
            "local_only attributes cannot be sent to external backends: "
            f"{labels}"
        )


def _legacy_evidence_items(context: AssembledContext) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for index, attribute in enumerate(context.attributes):
        label = str(attribute.get("label", "")).strip() or f"identity_{index}"
        value = str(attribute.get("value", "")).strip()
        elaboration = str(attribute.get("elaboration", "") or "").strip()
        if value and elaboration:
            content = f"{value} {elaboration}"
        else:
            content = value or elaboration
        items.append(
            EvidenceItem(
                source_type="identity",
                kind="attribute",
                raw_score=float(attribute.get("score", 0.0) or 0.0),
                normalized_score=0.0,
                final_score=float(attribute.get("score", 0.0) or 0.0),
                domain=str(attribute.get("domain", "")) or None,
                routing=str(attribute.get("routing", "local_only")),
                status=str(attribute.get("status", "active")),
                title_or_label=label,
                content=content,
                item_id=f"legacy-identity:{label}:{index}",
                source=str(attribute.get("source", "")) or None,
            )
        )

    for index, item in enumerate(context.preference_summary["positive"] + context.preference_summary["negative"]):
        items.append(
            EvidenceItem(
                source_type="preference",
                kind=str(item.get("source", "preference")),
                raw_score=float(item.get("score", 0.0) or 0.0),
                normalized_score=0.0,
                final_score=float(item.get("score", 0.0) or 0.0),
                domain=str(item.get("category", "")) or None,
                routing=str(item.get("routing", "local_only")),
                status=str(item.get("status", "summary")),
                title_or_label=str(item.get("subject", item.get("summary", f"preference_{index}"))),
                content=str(item.get("summary", "")).strip(),
                item_id=f"legacy-preference:{index}",
                source=str(item.get("source", "preference")),
            )
        )

    for index, chunk in enumerate(context.artifact_chunks):
        items.append(
            EvidenceItem(
                source_type="artifact",
                kind="artifact_chunk",
                raw_score=float(chunk.get("score", 0.0) or 0.0),
                normalized_score=0.0,
                final_score=float(chunk.get("score", 0.0) or 0.0),
                domain=str(chunk.get("domain", "")) or None,
                routing=str(chunk.get("routing", "local_only")),
                status="supporting",
                title_or_label=str(chunk.get("title", "Untitled artifact")),
                content=str(chunk.get("content", "")).strip(),
                item_id=f"legacy-artifact:{chunk.get('id', index)}",
                source=str(chunk.get("title", "Untitled artifact")),
                artifact_id=str(chunk.get("artifact_id", chunk.get("id", index))),
            )
        )

    return sorted(items, key=lambda item: (item.final_score, item.title_or_label), reverse=True)


def _visible_evidence_items(context: AssembledContext, target_backend: str) -> list[EvidenceItem]:
    items = list(context.evidence_items) if context.evidence_items else _legacy_evidence_items(context)
    if target_backend in ("local", "private_server"):
        return items
    return [item for item in items if item.routing != "local_only" and item.source_type != "artifact"]


def _format_grounded_context(context: AssembledContext, target_backend: str) -> str:
    visible_items = _visible_evidence_items(context, target_backend)
    if not visible_items:
        return "(no grounded context retrieved)"

    lines: list[str] = []
    for item in visible_items:
        if item.source_type == "artifact":
            excerpt = item.content.strip()
            if len(excerpt) > ARTIFACT_EXCERPT_MAX_CHARS:
                excerpt = excerpt[: ARTIFACT_EXCERPT_MAX_CHARS - 3].rstrip() + "..."
            chunk_label = ""
            if item.item_id.startswith("artifact:") or item.item_id.startswith("legacy-artifact:"):
                for chunk in context.artifact_chunks:
                    chunk_id = str(chunk.get("id", ""))
                    if item.item_id.endswith(chunk_id):
                        chunk_label = f" [chunk {int(chunk.get('chunk_index', 0)) + 1}]"
                        break
            lines.append(f"- [artifact] {item.title_or_label}{chunk_label}: {excerpt}")
            continue

        prefix = "identity" if item.source_type == "identity" else "preference"
        lines.append(f"- [{prefix}] {item.title_or_label}: {item.content}")
    return "\n".join(lines)


def _format_confidence_guidance(confidence: str | None) -> str:
    if confidence == "medium_confidence":
        return f"\n{_MEDIUM_CONFIDENCE_HINT}"
    if confidence == "low_confidence":
        return f"\n{_LOW_CONFIDENCE_HINT}"
    return ""


def _format_artifact_guidance(context: AssembledContext) -> str:
    if context.source_profile != "artifact_grounded_self":
        return ""
    return (
        "\nArtifact-grounded answer guidance:\n"
        "- Use uploaded artifacts as observed evidence.\n"
        "- Do not turn artifact-only evidence into stable identity or preference claims.\n"
        "- If a question asks for favorites, priorities, or rankings, only state that directly when the artifact explicitly ranks them or canonical preferences support it.\n"
        "- Otherwise answer with observed wording such as 'From your uploaded recipes, I found...' and list the grounded items."
    )


def _format_voice_guidance(context: AssembledContext, target_backend: str) -> str:
    profile = context.voice_profile
    if profile is None:
        return ""

    def _visible_lines(items) -> list[str]:
        if target_backend in ("local", "private_server"):
            return [item.text for item in items]
        return [item.text for item in items if item.routing != "local_only"]

    identity_lines = _visible_lines(profile.identity_lines)
    preference_lines = _visible_lines(profile.preference_lines)
    avoid_lines = _visible_lines(profile.avoid_lines)
    exemplar_lines = _visible_lines(profile.exemplar_lines) if target_backend in ("local", "private_server") else []

    if not any([identity_lines, preference_lines, avoid_lines, exemplar_lines]):
        return ""

    lines = ["", "Voice guidance:"]
    if identity_lines:
        lines.append("- Stable voice traits:")
        lines.extend(f"  - {line}" for line in identity_lines)
    if preference_lines:
        lines.append("- Voice preferences:")
        lines.extend(f"  - {line}" for line in preference_lines)
    if avoid_lines:
        lines.append("- Avoid:")
        lines.extend(f"  - {line}" for line in avoid_lines)
    if exemplar_lines:
        lines.append("- Local exemplar snippets:")
        lines.extend(f"  - {line}" for line in exemplar_lines)

    return "\n".join(lines)


def build_prompt(
    context: AssembledContext,
    target_backend: str = "local",
    *,
    enforce_routing: bool = True,
    confidence: str | None = None,
) -> list[dict]:
    """Build the final message array for response generation."""
    if enforce_routing:
        _assert_routing(
            context.attributes,
            context.preference_attributes,
            context.artifact_chunks,
            target_backend,
        )

    grounded_context = _format_grounded_context(context, target_backend)
    voice_guidance = _format_voice_guidance(context, target_backend)
    artifact_guidance = _format_artifact_guidance(context)
    confidence_guidance = _format_confidence_guidance(confidence)
    system_message = {
        "role": "system",
        "content": SYSTEM_PROMPT_TEMPLATE.format(
            grounded_context=grounded_context,
            voice_guidance=voice_guidance,
            artifact_guidance=artifact_guidance,
            confidence_guidance=confidence_guidance,
        ),
    }

    messages = [system_message]
    messages.extend(context.session_history)
    messages.append({"role": "user", "content": context.input_text})
    return messages
