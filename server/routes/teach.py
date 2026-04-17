"""Teach/onboarding routes."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from engine.interview_capture import save_preview_attributes
from engine.privacy_broker import PrivacyBroker
from engine.security_posture import inspect_security_posture
from engine.setup_state import build_recommended_profiles, get_app_settings, get_provider_statuses
from engine.teach_planner import (
    get_next_questions,
    get_question,
    mark_question_answered,
    record_question_feedback,
)
from server.db import get_db_connection
from server.models.schemas import (
    AttributeResponse,
    CapturePreviewWriteItem,
    PrivacyProfileOption,
    ProviderStatusResponse,
    SecurityCheckResponse,
    SecurityPostureResponse,
    TeachBootstrapResponse,
    TeachCard,
    TeachQuestionAnswerRequest,
    TeachQuestionFeedbackRequest,
    TeachQuestionResponse,
    TeachQuestionsResponse,
)

router = APIRouter(tags=["teach"])


def _accepted_to_dicts(items: list[CapturePreviewWriteItem] | list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
        else:
            normalized.append(item.model_dump())
    return normalized


def _serialize_questions(items) -> list[TeachQuestionResponse]:
    return [
        TeachQuestionResponse(
            id=item.id,
            prompt=item.prompt,
            domain=item.domain,
            intent_key=item.intent_key,
            source=item.source,  # type: ignore[arg-type]
            status=item.status,
            priority=item.priority,
        )
        for item in items
    ]


def _profile_option(profile: dict[str, object]) -> PrivacyProfileOption:
    return PrivacyProfileOption(
        code=cast(Any, str(profile["code"])),
        label=str(profile["label"]),
        description=str(profile["description"]),
        default_backend=cast(Any, str(profile["default_backend"])),
        requires_external_provider=bool(profile["requires_external_provider"]),
        available=bool(profile["available"]),
        recommended=bool(profile["recommended"]),
    )


def _serialize_bootstrap(request: Request) -> TeachBootstrapResponse:
    with get_db_connection() as conn:
        settings = get_app_settings(conn)
        statuses = get_provider_statuses(conn)
        profiles = [_profile_option(profile) for profile in build_recommended_profiles(statuses)]
        questions = _serialize_questions(get_next_questions(conn, request.app.state.llm_config))

    posture = inspect_security_posture()
    security = SecurityPostureResponse(
        platform=str(posture["platform"]),
        supported=bool(posture["supported"]),
        checks=[SecurityCheckResponse(**check) for check in posture["checks"]],  # type: ignore[arg-type]
    )
    provider_statuses = [
        ProviderStatusResponse(
            provider=status.provider,  # type: ignore[arg-type]
            label=status.label,
            configured=status.configured,
            available=status.available,
            validated=status.validated,
            is_local=status.is_local,
            model=status.model,
            reason=status.reason,
        )
        for status in statuses
    ]

    cards = [
        TeachCard(
            type="welcome",
            title="Teach the engine",
            body="Share what matters, skip anything, and come back anytime.",
            payload={"onboarding_completed": settings["onboarding_completed"]},
        ),
        TeachCard(
            type="privacy_setup",
            title="Choose your model profile",
            body="Pick a privacy profile that matches your machine and provider setup.",
            payload={"recommended_profiles": [profile.model_dump(mode="json") for profile in profiles]},
        ),
        TeachCard(
            type="security_setup",
            title="Review local security",
            body="These recommendations help protect your local identity data.",
            payload={"security_checks": [check.model_dump(mode="json") for check in security.checks]},
        ),
    ]
    if questions:
        cards.append(
            TeachCard(
                type="question",
                title="Next question",
                body=questions[0].prompt,
                payload={"question_id": questions[0].id, "domain": questions[0].domain},
            )
        )

    return TeachBootstrapResponse(
        onboarding_completed=bool(settings["onboarding_completed"]),
        active_profile=cast(str | None, settings["active_profile"]),
        preferred_backend=cast(Any, settings["preferred_backend"]),
        providers=provider_statuses,
        profiles=profiles,
        security_posture=security,
        cards=cards,
        questions=questions,
    )


@router.get("/teach/bootstrap", response_model=TeachBootstrapResponse)
def bootstrap(request: Request) -> TeachBootstrapResponse:
    """Return onboarding status, setup recommendations, and the next Teach cards."""
    return _serialize_bootstrap(request)


@router.get("/teach/questions", response_model=TeachQuestionsResponse)
def questions(request: Request) -> TeachQuestionsResponse:
    """Return the next planned Teach questions."""
    with get_db_connection() as conn:
        items = get_next_questions(conn, request.app.state.llm_config)
    return TeachQuestionsResponse(questions=_serialize_questions(items))


@router.post("/teach/questions/{question_id}/answer")
def answer_question(
    question_id: str,
    payload: TeachQuestionAnswerRequest,
    request: Request,
) -> dict[str, object]:
    """Save a Teach answer by extracting structured attributes and persisting them."""
    with get_db_connection() as conn:
        question = get_question(conn, question_id)
        if question is None:
            raise HTTPException(status_code=404, detail="teach question not found")
        if not payload.answer.strip():
            raise HTTPException(status_code=422, detail="answer is required")

        accepted = payload.accepted
        if accepted is None:
            result = PrivacyBroker(request.app.state.llm_config).extract_interview_attributes(
                question.prompt,
                payload.answer,
                task_type="teach_answer_extraction",
            )
            accepted = [
                {
                    **item,
                    "domain": question.domain or "personality",
                }
                for item in result.content
            ]

        saved = save_preview_attributes(conn, _accepted_to_dicts(accepted))
        mark_question_answered(conn, question_id)

    return {
        "attributes_saved": len(saved),
        "attributes": [
            AttributeResponse(
                id=str(item["id"]),
                domain=str(item["domain"]),
                label=str(item["label"]),
                value=str(item["value"]),
                elaboration=item.get("elaboration"),
                mutability=str(item["mutability"]),
                source=str(item["source"]),
                confidence=float(item["confidence"]),
                routing=str(item["routing"]),
                status=str(item["status"]),
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                last_confirmed=item["last_confirmed"],
            ).model_dump(mode="json")
            for item in saved
        ],
        "next": _serialize_bootstrap(request).model_dump(mode="json"),
    }


@router.post("/teach/questions/{question_id}/feedback")
def feedback(
    question_id: str,
    payload: TeachQuestionFeedbackRequest,
    request: Request,
) -> TeachBootstrapResponse:
    """Record Teach question feedback and return the refreshed queue."""
    _ = request
    with get_db_connection() as conn:
        question = get_question(conn, question_id)
        if question is None:
            raise HTTPException(status_code=404, detail="teach question not found")
        record_question_feedback(conn, question_id, payload.feedback)
    return _serialize_bootstrap(request)
