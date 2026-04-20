"""Teach/onboarding routes."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from engine.interview_capture import save_preview_attributes
from engine.privacy_broker import (
    AuditedExternalExtractionConsentRequiredError,
    PrivacyBroker,
)
from engine.security_posture import inspect_security_posture
from engine.setup_state import (
    build_privacy_preferences,
    build_recommended_profiles,
    get_app_settings,
    get_provider_statuses,
    resolve_active_provider_config,
)
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
    PrivacyPreferenceOption,
    PrivacyProfileOption,
    ProviderCredentialField,
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


def _external_extraction_consent_response() -> JSONResponse:
    message = "Raw user input requires explicit consent before external extraction."
    return JSONResponse(
        {
            "error": "external_extraction_consent_required",
            "detail": message,
            "message": message,
        },
        status_code=409,
    )


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
        provider_scope=cast(Any, str(profile["provider_scope"])),
        provider_options=[str(provider) for provider in cast(list[object], profile["provider_options"])],
        recommended_provider=cast(str | None, profile["recommended_provider"]),
        recommendation_reason=str(profile["recommendation_reason"]),
        requires_external_provider=bool(profile["requires_external_provider"]),
        available=bool(profile["available"]),
        recommended=bool(profile["recommended"]),
    )


def _privacy_preference_option(option: dict[str, str]) -> PrivacyPreferenceOption:
    return PrivacyPreferenceOption(
        code=cast(Any, option["code"]),
        label=option["label"],
        description=option["description"],
    )


def _provider_status_response(status) -> ProviderStatusResponse:
    return ProviderStatusResponse(
        provider=status.provider,
        label=status.label,
        deployment=cast(Any, getattr(status, "deployment", "local" if status.is_local else "external")),
        trust_boundary=cast(
            Any,
            getattr(status, "trust_boundary", "self_hosted" if status.is_local else "external"),
        ),
        auth_strategy=cast(Any, getattr(status, "auth_strategy", "none" if status.is_local else "api_key")),
        configured=status.configured,
        available=status.available,
        validated=status.validated,
        is_local=status.is_local,
        description=getattr(status, "description", None),
        setup_hint=getattr(status, "setup_hint", None),
        credential_fields=[
            ProviderCredentialField(
                name=field.name,
                label=field.label,
                input_type=field.input_type,
                placeholder=field.placeholder,
                secret=field.secret,
            )
            for field in getattr(status, "credential_fields", [])
        ],
        model=status.model,
        reason=status.reason,
    )


def _serialize_bootstrap(request: Request) -> TeachBootstrapResponse:
    with get_db_connection() as conn:
        settings = get_app_settings(conn)
        statuses = get_provider_statuses(conn)
        privacy_preference = cast(str | None, settings["privacy_preference"])
        profiles = [
            _profile_option(profile)
            for profile in build_recommended_profiles(statuses, privacy_preference)
        ]
        questions = _serialize_questions(
            get_next_questions(
                conn,
                resolve_active_provider_config(conn, request.app.state.llm_config),
            )
        )

    posture = inspect_security_posture()
    security = SecurityPostureResponse(
        platform=str(posture["platform"]),
        supported=bool(posture["supported"]),
        checks=[SecurityCheckResponse(**check) for check in posture["checks"]],  # type: ignore[arg-type]
    )
    provider_statuses = [_provider_status_response(status) for status in statuses]
    preference_options = [
        _privacy_preference_option(option) for option in build_privacy_preferences()
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
        privacy_preference=cast(Any, settings["privacy_preference"]),
        privacy_preferences=preference_options,
        active_profile=cast(str | None, settings["active_profile"]),
        preferred_provider=cast(str | None, settings["preferred_provider"]),
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
        items = get_next_questions(
            conn,
            resolve_active_provider_config(conn, request.app.state.llm_config),
        )
    return TeachQuestionsResponse(questions=_serialize_questions(items))


@router.post("/teach/questions/{question_id}/answer", response_model=None)
def answer_question(
    question_id: str,
    payload: TeachQuestionAnswerRequest,
    request: Request,
) -> dict[str, object] | JSONResponse:
    """Save a Teach answer by extracting structured attributes and persisting them."""
    try:
        with get_db_connection() as conn:
            question = get_question(conn, question_id)
            if question is None:
                raise HTTPException(status_code=404, detail="teach question not found")
            if not payload.answer.strip():
                raise HTTPException(status_code=422, detail="answer is required")
            provider_config = resolve_active_provider_config(conn, request.app.state.llm_config)

            accepted = payload.accepted
            if accepted is None:
                broker = PrivacyBroker(provider_config)
                if payload.allow_external_extraction:
                    result = broker.extract_interview_attributes(
                        question.prompt,
                        payload.answer,
                        task_type="teach_answer_extraction",
                        allow_external_input=True,
                    )
                else:
                    result = broker.extract_interview_attributes(
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
    except AuditedExternalExtractionConsentRequiredError:
        return _external_extraction_consent_response()

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
