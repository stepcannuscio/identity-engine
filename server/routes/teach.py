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
from engine.reflection_session_engine import (
    process_reflection_turn,
    start_reflection_session,
)
from engine.security_posture import resolve_security_posture
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
from engine.staged_signal_reviewer import (
    accept_signal,
    count_pending_signals,
    dismiss_signal,
    list_pending_signals,
)
from engine.contradiction_detector import (
    dismiss_contradiction,
    resolve_contradiction,
)
from engine.synthesis_engine import (
    accept_synthesis,
    dismiss_synthesis,
    generate_synthesis_narrative,
    get_synthesis_by_id,
    list_pending_cross_domain_intelligence,
    refresh_cross_domain_intelligence,
)
from server.db import get_db_connection
from server.models.schemas import (
    AttributeResponse,
    CapturePreviewWriteItem,
    ContradictionActionResponse,
    ContradictionFlagResponse,
    CrossDomainSynthesisResponse,
    PrivacyPreferenceOption,
    PrivacyProfileOption,
    ProviderCredentialField,
    ProviderStatusResponse,
    ReflectionStartResponse,
    ReflectionTurnRequest,
    ReflectionTurnResponse,
    SecurityCheckResponse,
    SecurityPostureResponse,
    StagedSessionSignalActionResponse,
    StagedSessionSignalResponse,
    StagedSessionSignalsResponse,
    SuggestedAttributeUpdateItem,
    SynthesisActionResponse,
    TeachBootstrapResponse,
    TeachCard,
    TeachQuestionAnswerRequest,
    TeachQuestionFeedbackRequest,
    TeachQuestionResponse,
    TeachQuestionsResponse,
    TeachSynthesisResponse,
)

router = APIRouter(tags=["teach"])


def _external_extraction_consent_response(provider_label: str | None = None) -> JSONResponse:
    message = "Raw user input requires explicit consent before external extraction."
    return JSONResponse(
        {
            "error": "external_extraction_consent_required",
            "detail": message,
            "message": message,
            "provider_label": provider_label,
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


def _serialize_staged_signals(items) -> list[StagedSessionSignalResponse]:
    return [
        StagedSessionSignalResponse(
            id=item.id,
            session_id=item.session_id,
            exchange_index=item.exchange_index,
            signal_type=cast(Any, item.signal_type),
            payload=cast(dict[str, object], item.payload),
            created_at=item.created_at,
        )
        for item in items
    ]


def _serialize_syntheses(items) -> list[CrossDomainSynthesisResponse]:
    return [
        CrossDomainSynthesisResponse(
            id=item.id,
            theme_label=item.theme_label,
            domains_involved=list(item.domains_involved),
            strength=item.strength,
            synthesis_text=item.synthesis_text,
            evidence_ids=list(item.evidence_ids),
            status=cast(Any, item.status),
            created_at=item.created_at,
        )
        for item in items
    ]


def _serialize_contradictions(items) -> list[ContradictionFlagResponse]:
    return [
        ContradictionFlagResponse(
            id=item.id,
            attribute_a_id=item.attribute_a_id,
            attribute_a_domain=item.attribute_a_domain,
            attribute_a_label=item.attribute_a_label,
            attribute_a_value=item.attribute_a_value,
            attribute_b_id=item.attribute_b_id,
            attribute_b_domain=item.attribute_b_domain,
            attribute_b_label=item.attribute_b_label,
            attribute_b_value=item.attribute_b_value,
            polarity_axis=item.polarity_axis,
            confidence=item.confidence,
            status=cast(Any, item.status),
            created_at=item.created_at,
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
        staged_signal_count = count_pending_signals(conn)
        staged_signals = _serialize_staged_signals(list_pending_signals(conn, limit=3))
        cross_domain = list_pending_cross_domain_intelligence(conn)
        syntheses = _serialize_syntheses(cross_domain.syntheses[:3])
        contradictions = _serialize_contradictions(cross_domain.contradictions[:3])

        posture = resolve_security_posture(conn)

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
    if staged_signal_count:
        cards.append(
            TeachCard(
                type="conversation_signal",
                title="From your conversations",
                body="Review passive learning signals captured from recent query sessions.",
                payload={
                    "count": staged_signal_count,
                    "signals": [item.model_dump(mode="json") for item in staged_signals],
                },
            )
        )
    if syntheses or contradictions:
        cards.append(
            TeachCard(
                type="synthesis_review",
                title="Themes and tensions",
                body="Review patterns the engine noticed across multiple domains of your identity.",
                payload={
                    "syntheses": [item.model_dump(mode="json") for item in syntheses],
                    "contradictions": [item.model_dump(mode="json") for item in contradictions],
                },
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


@router.get("/teach/conversation-signals", response_model=StagedSessionSignalsResponse)
def conversation_signals(request: Request) -> StagedSessionSignalsResponse:
    """Return staged passive-learning signals awaiting Teach review."""
    _ = request
    with get_db_connection() as conn:
        items = list_pending_signals(conn)
    return StagedSessionSignalsResponse(signals=_serialize_staged_signals(items))


@router.get("/teach/synthesis", response_model=TeachSynthesisResponse)
def teach_synthesis(request: Request) -> TeachSynthesisResponse:
    """Return pending cross-domain syntheses and contradiction flags."""
    _ = request
    with get_db_connection() as conn:
        refresh_cross_domain_intelligence(conn)
        items = list_pending_cross_domain_intelligence(conn)
    return TeachSynthesisResponse(
        syntheses=_serialize_syntheses(items.syntheses),
        contradictions=_serialize_contradictions(items.contradictions),
    )


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
    except AuditedExternalExtractionConsentRequiredError as exc:
        provider_label = getattr(exc.audit, "provider", None)
        if provider_label == "private_server":
            provider_label = "your private server"
        return _external_extraction_consent_response(provider_label=provider_label)

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


@router.post(
    "/teach/conversation-signals/{signal_id}/accept",
    response_model=StagedSessionSignalActionResponse,
)
def accept_conversation_signal(signal_id: str, request: Request) -> StagedSessionSignalActionResponse:
    """Accept and promote one staged conversation-derived signal."""
    _ = request
    with get_db_connection() as conn:
        try:
            result = accept_signal(conn, signal_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StagedSessionSignalActionResponse(
        signal_id=result.signal_id,
        status=cast(Any, result.status),
        attributes_saved=result.attributes_saved,
        preference_signals_saved=result.preference_signals_saved,
    )


@router.post(
    "/teach/conversation-signals/{signal_id}/dismiss",
    response_model=StagedSessionSignalActionResponse,
)
def dismiss_conversation_signal(signal_id: str, request: Request) -> StagedSessionSignalActionResponse:
    """Dismiss one staged conversation-derived signal."""
    _ = request
    with get_db_connection() as conn:
        try:
            result = dismiss_signal(conn, signal_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StagedSessionSignalActionResponse(
        signal_id=result.signal_id,
        status=cast(Any, result.status),
        attributes_saved=result.attributes_saved,
        preference_signals_saved=result.preference_signals_saved,
    )


@router.post(
    "/teach/synthesis/{synthesis_id}/accept",
    response_model=SynthesisActionResponse,
)
def accept_synthesis_item(synthesis_id: str, request: Request) -> SynthesisActionResponse:
    """Accept a staged cross-domain synthesis; attempts local narrative generation."""
    with get_db_connection() as conn:
        provider_config = resolve_active_provider_config(conn, request.app.state.llm_config)
        try:
            synthesis_row = get_synthesis_by_id(conn, synthesis_id)
            if synthesis_row is None:
                raise HTTPException(status_code=404, detail="synthesis not found")
            narrative = generate_synthesis_narrative(synthesis_row, provider_config)
            result = accept_synthesis(conn, synthesis_id, narrative=narrative)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SynthesisActionResponse(
        synthesis_id=result.synthesis_id,
        status=cast(Any, result.status),
        narrative_generated=result.narrative_generated,
    )


@router.post(
    "/teach/synthesis/{synthesis_id}/dismiss",
    response_model=SynthesisActionResponse,
)
def dismiss_synthesis_item(synthesis_id: str, request: Request) -> SynthesisActionResponse:
    """Dismiss a staged cross-domain synthesis."""
    _ = request
    with get_db_connection() as conn:
        try:
            result = dismiss_synthesis(conn, synthesis_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SynthesisActionResponse(
        synthesis_id=result.synthesis_id,
        status=cast(Any, result.status),
        narrative_generated=result.narrative_generated,
    )


@router.post(
    "/teach/contradictions/{contradiction_id}/resolve",
    response_model=ContradictionActionResponse,
)
def resolve_contradiction_item(contradiction_id: str, request: Request) -> ContradictionActionResponse:
    """Mark a contradiction flag as resolved (user has addressed the tension)."""
    _ = request
    with get_db_connection() as conn:
        try:
            result = resolve_contradiction(conn, contradiction_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContradictionActionResponse(
        contradiction_id=result.contradiction_id,
        status=cast(Any, result.status),
    )


@router.post(
    "/teach/contradictions/{contradiction_id}/dismiss",
    response_model=ContradictionActionResponse,
)
def dismiss_contradiction_item(contradiction_id: str, request: Request) -> ContradictionActionResponse:
    """Dismiss a contradiction flag (user says it is not a real tension)."""
    _ = request
    with get_db_connection() as conn:
        try:
            result = dismiss_contradiction(conn, contradiction_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContradictionActionResponse(
        contradiction_id=result.contradiction_id,
        status=cast(Any, result.status),
    )


@router.post("/teach/reflection/start", response_model=ReflectionStartResponse)
def start_reflection(request: Request) -> ReflectionStartResponse:
    """Start a new deep reflection session seeded from synthesis and temporal data."""
    with get_db_connection() as conn:
        provider_config = resolve_active_provider_config(conn, request.app.state.llm_config)
        session_id, state, first_question = start_reflection_session(conn, provider_config)
    if not hasattr(request.app.state, "reflection_sessions"):
        request.app.state.reflection_sessions = {}
    request.app.state.reflection_sessions[session_id] = state
    return ReflectionStartResponse(
        session_id=session_id,
        first_question=first_question,
        seed_domain=state.seed_domain,
    )


@router.post("/teach/reflection/turn", response_model=ReflectionTurnResponse)
def reflection_turn(
    payload: ReflectionTurnRequest,
    request: Request,
) -> ReflectionTurnResponse:
    """Process one turn in an active reflection session."""
    sessions: dict[str, Any] = getattr(request.app.state, "reflection_sessions", {})
    state = sessions.get(payload.session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="reflection session not found")
    if not payload.user_message.strip():
        raise HTTPException(status_code=422, detail="user_message is required")
    with get_db_connection() as conn:
        provider_config = resolve_active_provider_config(conn, request.app.state.llm_config)
        result = process_reflection_turn(conn, state, payload.user_message, provider_config)
    return ReflectionTurnResponse(
        session_id=payload.session_id,
        next_question=result.next_question,
        suggested_updates=[
            SuggestedAttributeUpdateItem(
                domain=u.domain,
                label=u.label,
                value=u.value,
                confidence=u.confidence,
                elaboration=u.elaboration,
            )
            for u in result.suggested_updates
        ],
        themes_noticed=result.themes_noticed,
        staged_signal_ids=result.staged_signal_ids,
        turn_count=state.turn_count,
    )
