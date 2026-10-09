"""Modelos de datos de descubrimiento; no contienen lógica de persistencia."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class OpportunityCandidate:
    """Registro candidato extraído de una fuente oficial."""

    source: str
    title: str
    detail_url: str
    official_code: str | None = None
    organism: str | None = None
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceReport:
    """Resultado explícito de una consulta a la fuente.

    complete=False significa que la ausencia de registros no puede considerarse
    ausencia de oportunidades.
    """
    source: str
    requested_url: str
    final_url: str
    http_status: int
    html_bytes: int
    candidates: tuple[OpportunityCandidate, ...]
    complete: bool
    warnings: tuple[str, ...] = ()
    total_records: int | None = None
    page: int = 1
    page_size: int = 30
    deadline_state: str = "A"
