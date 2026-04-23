"""Structured voice-profile assembly for rewrite and drafting queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.preference_summary import PreferenceSummaryItem, PreferenceSummaryPayload
from engine.voice_feature_extractor import load_baseline


@dataclass(frozen=True)
class VoiceGuidanceItem:
    """One routed voice-guidance line."""

    text: str
    routing: str


@dataclass(frozen=True)
class VoiceProfile:
    """Bounded voice guidance assembled from grounded context."""

    identity_lines: list[VoiceGuidanceItem]
    preference_lines: list[VoiceGuidanceItem]
    avoid_lines: list[VoiceGuidanceItem]
    exemplar_lines: list[VoiceGuidanceItem]
    contains_local_only: bool

    @property
    def has_visible_guidance(self) -> bool:
        return bool(
            self.identity_lines
            or self.preference_lines
            or self.avoid_lines
            or self.exemplar_lines
        )


_VOICE_SIGNAL_SUBJECT_LABELS = {
    "authentic_voice": "keep the response sounding recognizably like me",
    "formal_tone": "overly formal tone",
    "wordy_phrasing": "wordy phrasing",
    "rhythm_mismatch": "rhythm that does not match my natural cadence",
    "overdone_style_markers": "overdone stylistic markers or quirks",
}


def _content_line(attribute: dict) -> str:
    value = str(attribute.get("value", "")).strip()
    elaboration = str(attribute.get("elaboration", "") or "").strip()
    if value and elaboration:
        return f"{value} {elaboration}"
    return value or elaboration


def _label_hint(label: str) -> str | None:
    lowered = label.strip().lower()
    if not lowered:
        return None
    if lowered == "writing_style":
        return "writing style"
    if lowered == "tone":
        return "tone"
    return lowered.replace("_", " ")


def _format_identity_line(attribute: dict) -> str | None:
    content = _content_line(attribute)
    if not content:
        return None
    hint = _label_hint(str(attribute.get("label", "")))
    if hint:
        return f"{hint}: {content}"
    return content


def _is_negative_voice_subject(subject: str) -> bool:
    lowered = subject.lower()
    return any(
        token in lowered
        for token in ("avoid", "never", "less_", "too_", "not_", "overdone", "wordy", "formal")
    )


def _format_preference_line(item: PreferenceSummaryItem) -> str | None:
    summary = str(item.get("summary", "")).strip()
    if not summary:
        return None
    return summary


def _format_avoid_line(item: PreferenceSummaryItem) -> str | None:
    subject = str(item.get("subject", "")).strip()
    summary = str(item.get("summary", "")).strip()
    if subject in _VOICE_SIGNAL_SUBJECT_LABELS:
        return f"Avoid {_VOICE_SIGNAL_SUBJECT_LABELS[subject]}."
    if summary:
        return summary
    if not subject:
        return None
    return f"Avoid {subject.replace('_', ' ')}."


def _artifact_excerpt(chunk: dict, *, max_chars: int = 220) -> str | None:
    content = str(chunk.get("content", "")).strip()
    if not content:
        return None
    if len(content) > max_chars:
        return content[: max_chars - 3].rstrip() + "..."
    return content


def build_voice_profile(
    *,
    source_profile: str,
    attributes: list[dict],
    preference_attributes: list[dict],
    preference_summary: PreferenceSummaryPayload,
    artifact_chunks: list[dict],
    conn: Any = None,
) -> VoiceProfile | None:
    """Return a bounded voice profile for explicit voice-generation tasks."""
    if source_profile != "voice_generation":
        return None

    identity_lines: list[str] = []
    identity_items: list[VoiceGuidanceItem] = []
    for attribute in attributes:
        if str(attribute.get("domain", "")) != "voice":
            continue
        line = _format_identity_line(attribute)
        if line and line not in identity_lines:
            identity_lines.append(line)
            identity_items.append(
                VoiceGuidanceItem(
                    text=line,
                    routing=str(attribute.get("routing", "local_only")),
                )
            )

    preference_lines: list[str] = []
    avoid_lines: list[str] = []
    preference_items: list[VoiceGuidanceItem] = []
    avoid_items: list[VoiceGuidanceItem] = []

    for attribute in preference_attributes:
        if str(attribute.get("domain", "")) != "voice":
            continue
        line = _content_line(attribute)
        if not line:
            continue
        if _is_negative_voice_subject(str(attribute.get("label", ""))):
            if line not in avoid_lines:
                avoid_lines.append(line)
                avoid_items.append(
                    VoiceGuidanceItem(
                        text=line,
                        routing=str(attribute.get("routing", "local_only")),
                    )
                )
            continue
        if line not in preference_lines:
            preference_lines.append(line)
            preference_items.append(
                VoiceGuidanceItem(
                    text=line,
                    routing=str(attribute.get("routing", "local_only")),
                )
            )

    for item in preference_summary.get("positive", []):
        category = str(item.get("category", "")).lower()
        if category not in {"voice", "writing_style"}:
            continue
        line = _format_preference_line(item)
        if line and line not in preference_lines:
            preference_lines.append(line)
            preference_items.append(
                VoiceGuidanceItem(
                    text=line,
                    routing=str(item.get("routing", "local_only")),
                )
            )

    for item in preference_summary.get("negative", []):
        category = str(item.get("category", "")).lower()
        if category not in {"voice", "writing_style"}:
            continue
        line = _format_avoid_line(item)
        if line and line not in avoid_lines:
            avoid_lines.append(line)
            avoid_items.append(
                VoiceGuidanceItem(
                    text=line,
                    routing=str(item.get("routing", "local_only")),
                )
            )

    exemplar_lines: list[str] = []
    exemplar_items: list[VoiceGuidanceItem] = []
    for chunk in artifact_chunks:
        if str(chunk.get("domain", "") or "") != "voice":
            continue
        excerpt = _artifact_excerpt(chunk)
        if not excerpt:
            continue
        title = str(chunk.get("title", "Writing sample")).strip() or "Writing sample"
        line = f"{title}: {excerpt}"
        if line not in exemplar_lines:
            exemplar_lines.append(line)
            exemplar_items.append(VoiceGuidanceItem(text=line, routing="local_only"))
        if len(exemplar_lines) >= 2:
            break

    # Append learned structural guidance from accumulated voice observations.
    if conn is not None:
        try:
            baseline = load_baseline(conn)
            if baseline is not None:
                for guidance_line in baseline.to_guidance_lines():
                    if guidance_line not in preference_lines:
                        preference_lines.append(guidance_line)
                        preference_items.append(
                            VoiceGuidanceItem(text=guidance_line, routing="local_only")
                        )
        except Exception:
            pass

    profile = VoiceProfile(
        identity_lines=identity_items[:4],
        preference_lines=preference_items[:4],
        avoid_lines=avoid_items[:4],
        exemplar_lines=exemplar_items[:2],
        contains_local_only=any(
            item.routing == "local_only"
            for item in [*identity_items, *preference_items, *avoid_items, *exemplar_items]
        ),
    )
    if not profile.has_visible_guidance:
        return None
    return profile
