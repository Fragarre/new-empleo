"""Orquestación del descubrimiento GVA y persistencia local opcional.

La ejecución no escribe datos por defecto. Para persistir, el operador debe
solicitarlo expresamente y el rastreo de la fuente debe estar completo.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import time
from typing import Callable

from .db import DEFAULT_DB, connect_database, initialize_database
from .details import DetailExtractionError, OpportunityDetails, fetch_detail
from .models import SourceReport
from .repository import finish_source_run, start_source_run, upsert_details
from .sources.sede_gva import SourceAccessError, SourceStructureError, discover_active_pages


@dataclass(frozen=True, slots=True)
class CollectionOutcome:
    source_complete: bool
    source_total: int | None
    discovered_links: int
    selected_for_detail: int
    details_parsed: int
    records_saved: int
    records_created: int
    records_updated: int
    detail_failures: tuple[str, ...]
    warnings: tuple[str, ...]
    persisted: bool
    collection_complete: bool


class CollectionError(RuntimeError):
    """La consulta no pudo completarse de forma utilizable."""


def collect_opportunities(
    opener,
    *,
    limit: int = 5,
    persist: bool = False,
    database_path: str | Path = DEFAULT_DB,
    page_size: int = 100,
    max_pages: int = 100,
    discovery_function: Callable | None = None,
    detail_fetcher: Callable | None = None,
) -> CollectionOutcome:
    """Descubre, analiza y opcionalmente guarda oportunidades oficiales GVA.

    `limit=5` hace que el modo de prueba consulte solo cinco PDFs. Usa
    `limit=0` para solicitar todos los detalles. La persistencia exige limit=0.
    Nunca guarda oportunidades si el rastreo no puede reconciliarse con la fuente.
    """
    if limit < 0:
        raise ValueError("limit debe ser >= 0")
    if persist and limit != 0:
        raise ValueError("Para persistir todos los resultados, usa limit=0")
    discover = discovery_function or discover_active_pages
    get_detail = detail_fetcher or fetch_detail

    connection = None
    source_run_id = None
    if persist:
        initialize_database(database_path)
        connection = connect_database(database_path)
        source_run_id = start_source_run(connection, "sede_gva_empleo_publico")
        connection.commit()

    try:
        report: SourceReport = discover(
            opener, page_size=page_size, max_pages=max_pages
        )
    except Exception as exc:
        if connection is not None and source_run_id is not None:
            finish_source_run(
                connection, source_run_id, "failed",
                error_type=type(exc).__name__,
                error_summary="La consulta oficial no pudo completarse.",
            )
            connection.commit()
            connection.close()
        raise CollectionError(
            f"Consulta GVA fallida ({type(exc).__name__})"
        ) from None

    if not report.complete:
        if connection is not None and source_run_id is not None:
            finish_source_run(
                connection, source_run_id, "failed",
                records_discovered=len(report.candidates),
                error_type="IncompleteSourceReport",
                error_summary="Rastreo incompleto: no se guardaron oportunidades.",
            )
            connection.commit()
            connection.close()
        return CollectionOutcome(
            source_complete=False,
            source_total=report.total_records,
            discovered_links=len(report.candidates),
            selected_for_detail=0,
            details_parsed=0,
            records_saved=0,
            records_created=0,
            records_updated=0,
            detail_failures=(),
            warnings=report.warnings,
            persisted=persist,
            collection_complete=False,
        )

    candidates = list(report.candidates)
    if limit:
        candidates = candidates[:limit]

    details: list[tuple[object, OpportunityDetails]] = []
    failures: list[str] = []
    for index, candidate in enumerate(candidates):
        if not candidate.official_code or not candidate.official_code.isdigit():
            failures.append(f"sin_codigo:{candidate.detail_url}")
            continue
        try:
            parsed = get_detail(opener, candidate.official_code)
            if parsed.official_code != candidate.official_code:
                raise DetailExtractionError("El código del detalle no coincide con el listado")
            details.append((candidate, parsed))
        except Exception as exc:
            failures.append(f"{candidate.official_code}:{type(exc).__name__}")
        if index + 1 < len(candidates):
            time.sleep(0.1)

    created_count = 0
    updated_count = 0
    saved_count = 0
    if connection is not None and source_run_id is not None:
        try:
            for _, parsed in details:
                _, created, changed = upsert_details(
                    connection, parsed, source_run_id=source_run_id
                )
                saved_count += 1
                if created:
                    created_count += 1
                elif changed:
                    updated_count += 1
            final_status = "partial" if failures else "success"
            finish_source_run(
                connection,
                source_run_id,
                final_status,
                records_discovered=len(report.candidates),
                records_processed=saved_count,
                error_type="DetailFetchFailures" if failures else None,
                error_summary=(
                    f"No se pudieron analizar {len(failures)} detalles."
                    if failures else None
                ),
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            finish_source_run(
                connection, source_run_id, "failed",
                records_discovered=len(report.candidates),
                records_processed=0,
                error_type=type(exc).__name__,
                error_summary="Falló la persistencia local; comprueba el estado de la base.",
            )
            connection.commit()
            raise CollectionError(
                f"Persistencia local fallida ({type(exc).__name__})"
            ) from None
        finally:
            connection.close()

    all_details_selected = limit == 0 or len(candidates) == len(report.candidates)
    all_details_parsed = len(details) == len(candidates) and not failures
    return CollectionOutcome(
        source_complete=True,
        source_total=report.total_records,
        discovered_links=len(report.candidates),
        selected_for_detail=len(candidates),
        details_parsed=len(details),
        records_saved=saved_count,
        records_created=created_count,
        records_updated=updated_count,
        detail_failures=tuple(failures),
        warnings=report.warnings,
        persisted=persist,
        collection_complete=all_details_selected and all_details_parsed,
    )


def outcome_as_dict(outcome: CollectionOutcome) -> dict:
    """Versión serializable de los resultados del proceso."""
    return asdict(outcome)
