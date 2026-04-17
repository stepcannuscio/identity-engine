"""Identity graph CRUD routes for attributes and domains."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from sqlite3 import IntegrityError

from fastapi import APIRouter, HTTPException, Request

from config.settings import EXTERNAL_OK
from server.db import get_db_connection
from server.models.schemas import (
    AttributeProvenanceResponse,
    AttributeResponse,
    AttributeUpdateRequest,
    CreateAttributeRequest,
    DomainSummary,
)
from server.services import build_attribute_provenance_response

router = APIRouter(tags=["attributes"])

_PROTECTED_DOMAINS = {"beliefs", "fears", "patterns", "relationships"}


class RoutingProtectedError(Exception):
    """Raised when a protected-domain attribute is routed to an external API."""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _attribute_fields_set(payload: AttributeUpdateRequest) -> set[str]:
    return set(getattr(payload, "model_fields_set", set()))


def _serialize_attribute(row) -> AttributeResponse:
    return AttributeResponse(
        id=str(row[0]),
        domain=str(row[1]),
        label=str(row[2]),
        value=str(row[3]),
        elaboration=row[4],
        mutability=str(row[5]),
        source=str(row[6]),
        confidence=float(row[7]),
        routing=str(row[8]),
        status=str(row[9]),
        created_at=row[10],
        updated_at=row[11],
        last_confirmed=row[12],
    )


def _fetch_attribute(conn, attribute_id: str):
    return conn.execute(
        """
        SELECT
            a.id,
            d.name,
            a.label,
            a.value,
            a.elaboration,
            a.mutability,
            a.source,
            a.confidence,
            a.routing,
            a.status,
            a.created_at,
            a.updated_at,
            a.last_confirmed
        FROM attributes a
        JOIN domains d ON d.id = a.domain_id
        WHERE a.id = ?
        """,
        (attribute_id,),
    ).fetchone()


def _get_domain_id(conn, domain: str) -> str:
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (domain,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="domain not found")
    return str(row[0])


def _routing_guard(domain: str, routing: str | None) -> None:
    if routing == EXTERNAL_OK and domain in _PROTECTED_DOMAINS:
        raise RoutingProtectedError()


@router.get("/attributes", response_model=list[AttributeResponse])
def list_attributes(request: Request, domain: str | None = None) -> list[AttributeResponse]:
    """List active attributes, optionally filtered by domain."""
    params: tuple[object, ...] = ()
    where = "WHERE a.status = 'active'"
    if domain:
        where += " AND d.name = ?"
        params = (domain,)
    with get_db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
                a.id,
                d.name,
                a.label,
                a.value,
                a.elaboration,
                a.mutability,
                a.source,
                a.confidence,
                a.routing,
                a.status,
                a.created_at,
                a.updated_at,
                a.last_confirmed
            FROM attributes a
            JOIN domains d ON d.id = a.domain_id
            {where}
            ORDER BY d.name, a.label
            """,
            params,
        ).fetchall()
    return [_serialize_attribute(row) for row in rows]


@router.get("/attributes/{attribute_id}", response_model=AttributeResponse)
def get_attribute(attribute_id: str, request: Request) -> AttributeResponse:
    """Return one attribute by id."""
    _ = request
    with get_db_connection() as conn:
        row = _fetch_attribute(conn, attribute_id)
    if row is None:
        raise HTTPException(status_code=404, detail="attribute not found")
    return _serialize_attribute(row)


@router.get(
    "/attributes/{attribute_id}/provenance",
    response_model=AttributeProvenanceResponse,
)
def get_attribute_provenance(
    attribute_id: str,
    request: Request,
) -> AttributeProvenanceResponse:
    """Return privacy-safe provenance details for one attribute."""
    _ = request
    with get_db_connection() as conn:
        row = _fetch_attribute(conn, attribute_id)
        if row is None:
            raise HTTPException(status_code=404, detail="attribute not found")
        attribute = _serialize_attribute(row)
        return build_attribute_provenance_response(conn, attribute)


@router.post("/attributes", response_model=AttributeResponse)
def create_attribute(payload: CreateAttributeRequest, request: Request) -> AttributeResponse:
    """Create a new attribute row."""
    _ = request
    _routing_guard(payload.domain, payload.routing)

    with get_db_connection() as conn:
        domain_id = _get_domain_id(conn, payload.domain)
        now = _utcnow()
        attribute_id = str(uuid.uuid4())
        try:
            conn.execute(
                """
                INSERT INTO attributes (
                    id, domain_id, label, value, elaboration, mutability, source, confidence,
                    routing, status, created_at, updated_at, last_confirmed
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    attribute_id,
                    domain_id,
                    payload.label,
                    payload.value,
                    payload.elaboration,
                    payload.mutability,
                    payload.source,
                    payload.confidence,
                    payload.routing,
                    now,
                    now,
                    None,
                ),
            )
            conn.commit()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="attribute already exists") from exc

        row = _fetch_attribute(conn, attribute_id)
    assert row is not None
    return _serialize_attribute(row)


@router.put("/attributes/{attribute_id}", response_model=AttributeResponse)
def update_attribute(
    attribute_id: str,
    payload: AttributeUpdateRequest,
    request: Request,
) -> AttributeResponse:
    """Update an attribute in place or supersede it when the value changes."""
    _ = request
    with get_db_connection() as conn:
        current = _fetch_attribute(conn, attribute_id)
        if current is None:
            raise HTTPException(status_code=404, detail="attribute not found")

        fields_set = _attribute_fields_set(payload)
        current_domain = str(current[1])
        next_routing = payload.routing if "routing" in fields_set else str(current[8])
        _routing_guard(current_domain, next_routing)

        value_changed = (
            "value" in fields_set
            and payload.value is not None
            and payload.value != current[3]
        )
        if value_changed:
            now = _utcnow()
            conn.execute(
                "UPDATE attributes SET status = 'superseded', updated_at = ? WHERE id = ?",
                (now, attribute_id),
            )
            conn.execute(
                """
                INSERT INTO attribute_history (
                    id,
                    attribute_id,
                    previous_value,
                    previous_confidence,
                    reason,
                    changed_at,
                    changed_by
                )
                VALUES (?, ?, ?, ?, ?, ?, 'user')
                """,
                (
                    str(uuid.uuid4()),
                    attribute_id,
                    current[3],
                    current[7],
                    "api update",
                    now,
                ),
            )
            new_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO attributes (
                    id, domain_id, label, value, elaboration, mutability, source, confidence,
                    routing, status, created_at, updated_at, last_confirmed
                )
                SELECT
                    ?,
                    a.domain_id,
                    a.label,
                    ?,
                    ?,
                    ?,
                    a.source,
                    ?,
                    ?,
                    'active',
                    ?,
                    ?,
                    a.last_confirmed
                FROM attributes a
                WHERE a.id = ?
                """,
                (
                    new_id,
                    payload.value,
                    (
                        payload.elaboration
                        if "elaboration" in fields_set
                        else current[4]
                    ),
                    (
                        payload.mutability
                        if "mutability" in fields_set and payload.mutability
                        else current[5]
                    ),
                    (
                        payload.confidence
                        if "confidence" in fields_set and payload.confidence is not None
                        else current[7]
                    ),
                    next_routing,
                    now,
                    now,
                    attribute_id,
                ),
            )
            conn.commit()
            created = _fetch_attribute(conn, new_id)
            assert created is not None
            return _serialize_attribute(created)

        assignments: list[str] = []
        values: list[object] = []
        if "elaboration" in fields_set:
            assignments.append("elaboration = ?")
            values.append(payload.elaboration)
        if "confidence" in fields_set and payload.confidence is not None:
            assignments.append("confidence = ?")
            values.append(payload.confidence)
        if "routing" in fields_set and payload.routing is not None:
            assignments.append("routing = ?")
            values.append(payload.routing)
        if "mutability" in fields_set and payload.mutability is not None:
            assignments.append("mutability = ?")
            values.append(payload.mutability)

        if assignments:
            assignments.append("updated_at = ?")
            values.append(_utcnow())
            values.append(attribute_id)
            conn.execute(
                f"UPDATE attributes SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
            conn.commit()

        row = _fetch_attribute(conn, attribute_id)
    assert row is not None
    return _serialize_attribute(row)


@router.delete("/attributes/{attribute_id}")
def delete_attribute(attribute_id: str, request: Request) -> dict[str, str]:
    """Soft-delete an attribute by retracting it."""
    _ = request
    with get_db_connection() as conn:
        row = _fetch_attribute(conn, attribute_id)
        if row is None:
            raise HTTPException(status_code=404, detail="attribute not found")
        conn.execute(
            "UPDATE attributes SET status = 'retracted', updated_at = ? WHERE id = ?",
            (_utcnow(), attribute_id),
        )
        conn.commit()
    return {"status": "ok"}


@router.post("/attributes/{attribute_id}/confirm", response_model=AttributeResponse)
def confirm_attribute(attribute_id: str, request: Request) -> AttributeResponse:
    """Update last_confirmed to now."""
    _ = request
    with get_db_connection() as conn:
        row = _fetch_attribute(conn, attribute_id)
        if row is None:
            raise HTTPException(status_code=404, detail="attribute not found")
        now = _utcnow()
        conn.execute(
            "UPDATE attributes SET last_confirmed = ?, updated_at = ? WHERE id = ?",
            (now, now, attribute_id),
        )
        conn.commit()
        updated = _fetch_attribute(conn, attribute_id)
    assert updated is not None
    return _serialize_attribute(updated)


@router.get("/domains", response_model=list[DomainSummary])
def list_domains(request: Request) -> list[DomainSummary]:
    """List domains with active-attribute counts."""
    _ = request
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT d.name, count(a.id)
            FROM domains d
            LEFT JOIN attributes a ON a.domain_id = d.id AND a.status = 'active'
            GROUP BY d.id, d.name
            ORDER BY d.name
            """
        ).fetchall()
    return [
        DomainSummary(domain=str(name), attribute_count=int(count))
        for name, count in rows
    ]
