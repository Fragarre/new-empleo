"""Persistencia idempotente de detalles GVA y trazabilidad de observaciones."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3

from .details import OpportunityDetails

TRACKED_FIELDS = (
    "title", "organism", "call_type", "test_type", "group", "total_places",
    "current_stage", "application_opens_on", "application_closes_on",
    "application_status", "process_status", "detail_url",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_source_run(connection: sqlite3.Connection, source_id: str, started_at: str | None = None) -> int:
    """Registra una ejecución antes de consultar la fuente."""
    timestamp = started_at or utc_now()
    cursor = connection.execute(
        "INSERT INTO source_runs (source_id, started_at, status) VALUES (?, ?, 'running')",
        (source_id, timestamp),
    )
    return int(cursor.lastrowid)


def finish_source_run(
    connection: sqlite3.Connection,
    run_id: int,
    status: str,
    records_discovered: int = 0,
    records_processed: int = 0,
    error_type: str | None = None,
    error_summary: str | None = None,
    finished_at: str | None = None,
) -> None:
    """Cierra una ejecución con estado explícito; no oculta fallos parciales."""
    if status not in {"success", "partial", "failed"}:
        raise ValueError("status final debe ser success, partial o failed")
    if records_discovered < 0 or records_processed < 0:
        raise ValueError("Los recuentos no pueden ser negativos")
    connection.execute(
        """UPDATE source_runs SET finished_at=?, status=?, records_discovered=?,
           records_processed=?, error_type=?, error_summary=? WHERE id=? AND status='running'""",
        (finished_at or utc_now(), status, records_discovered, records_processed,
         error_type, error_summary, run_id),
    )
    if connection.execute("SELECT changes()").fetchone()[0] != 1:
        raise ValueError("No existe una ejecución abierta con ese id")


def upsert_details(
    connection: sqlite3.Connection,
    details: OpportunityDetails,
    source_id: str = "sede_gva_empleo_publico",
    source_run_id: int | None = None,
    observed_at: str | None = None,
) -> tuple[int, bool, tuple[str, ...]]:
    """Inserta o actualiza un detalle y conserva cada observación.

    Devuelve (id, creado, campos_cambiados). Un campo opcional ausente no borra
    un valor anterior. Cada cambio queda respaldado por la URL y hash del PDF.
    """
    timestamp = observed_at or utc_now()
    source_record_id = details.official_code.strip() or details.detail_url
    if not details.title.strip() or not details.detail_url.startswith("https://sede.gva.es/es/detall-ocupacio-publica"):
        raise ValueError("El detalle debe tener título y URL oficial GVA")

    values = {
        "title": details.title,
        "organism": details.organism,
        "call_type": details.call_type,
        "test_type": details.test_type,
        "group": details.group,
        "total_places": details.total_places,
        "current_stage": details.current_stage,
        "application_opens_on": details.application_opens_on,
        "application_closes_on": details.application_closes_on,
        "application_status": details.application_status,
        "process_status": details.process_status,
        "detail_url": details.detail_url,
    }
    fields_changed: list[str] = []
    with connection:
        existing = connection.execute(
            "SELECT * FROM opportunities WHERE source_id=? AND source_record_id=?",
            (source_id, source_record_id),
        ).fetchone()

        if existing is None:
            cursor = connection.execute(
                """INSERT INTO opportunities (
                    source_id, source_record_id, official_code, title, organism, call_type,
                    test_type, group_code, total_places, detail_url, current_stage,
                    application_opens_on, application_closes_on, application_status,
                    process_status, first_seen_at, last_seen_at, last_verified_at,
                    last_content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, source_record_id, details.official_code, details.title,
                 details.organism, details.call_type, details.test_type, details.group,
                 details.total_places, details.detail_url, details.current_stage,
                 details.application_opens_on, details.application_closes_on,
                 details.application_status, details.process_status, timestamp,
                 timestamp, timestamp, details.pdf_sha256),
            )
            opportunity_id = int(cursor.lastrowid)
            created = True
        else:
            opportunity_id = int(existing["id"])
            created = False
            db_field = {"group": "group_code"}
            for field_name in TRACKED_FIELDS:
                column = db_field.get(field_name, field_name)
                new_value = values[field_name]
                old_value = existing[column]
                # "unknown" expresa falta de evidencia; no debe borrar un
                # estado que una observación anterior sí pudo determinar.
                if new_value is None or (
                    field_name in {"application_status", "process_status"}
                    and new_value == "unknown"
                ):
                    continue
                if str(old_value) != str(new_value):
                    fields_changed.append(field_name)
                    connection.execute(
                        """INSERT INTO opportunity_changes (
                            opportunity_id, source_run_id, changed_at, field_name,
                            previous_value, current_value, evidence_url, evidence_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (opportunity_id, source_run_id, timestamp, field_name,
                         None if old_value is None else str(old_value), str(new_value),
                         details.pdf_url, details.pdf_sha256),
                    )
            connection.execute(
                """UPDATE opportunities SET
                    title=COALESCE(?, title), organism=COALESCE(?, organism),
                    call_type=COALESCE(?, call_type), test_type=COALESCE(?, test_type),
                    group_code=COALESCE(?, group_code), total_places=COALESCE(?, total_places),
                    current_stage=COALESCE(?, current_stage),
                    application_opens_on=COALESCE(?, application_opens_on),
                    application_closes_on=COALESCE(?, application_closes_on),
                    application_status=COALESCE(?, application_status),
                    process_status=COALESCE(?, process_status),
                    detail_url=?, last_seen_at=?, last_verified_at=?, last_content_sha256=?
                   WHERE id=?""",
                (details.title, details.organism, details.call_type, details.test_type,
                 details.group, details.total_places, details.current_stage,
                 details.application_opens_on, details.application_closes_on,
                 details.application_status if details.application_status != "unknown" else None,
                 details.process_status if details.process_status != "unknown" else None,
                 details.detail_url, timestamp, timestamp, details.pdf_sha256, opportunity_id),
            )

        payload = json.dumps({
            "official_code": details.official_code,
            "title": details.title,
            "organism": details.organism,
            "call_type": details.call_type,
            "test_type": details.test_type,
            "group": details.group,
            "total_places": details.total_places,
            "current_stage": details.current_stage,
            "application_opens_on": details.application_opens_on,
            "application_closes_on": details.application_closes_on,
            "application_status": details.application_status,
            "process_status": details.process_status,
            "detail_url": details.detail_url,
        }, ensure_ascii=False, sort_keys=True)
        connection.execute(
            """INSERT OR IGNORE INTO opportunity_observations (
                opportunity_id, source_run_id, observed_at, detail_url,
                content_sha256, raw_text, parsed_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (opportunity_id, source_run_id, timestamp, details.detail_url,
             details.pdf_sha256, details.raw_text, payload),
        )
    return opportunity_id, created, tuple(fields_changed)
