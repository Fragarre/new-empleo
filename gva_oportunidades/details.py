"""Descarga y extracción de datos básicos del PDF oficial de detalle GVA."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import re
from urllib.parse import urlencode

from pypdf import PdfReader

DETAIL_URL = "https://sede.gva.es/es/detall-ocupacio-publica"
PORTLET = "es_gva_es_siac_portlet_SiacDetalleEmpleoPublicoNuevoGVA"


class DetailExtractionError(RuntimeError):
    """El PDF oficial no pudo leerse o no contiene los campos esperados."""


@dataclass(frozen=True, slots=True)
class OpportunityDetails:
    official_code: str
    title: str
    organism: str | None
    call_type: str | None
    test_type: str | None
    group: str | None
    total_places: int | None
    current_stage: str | None
    application_opens_on: str | None
    application_closes_on: str | None
    application_status: str
    process_status: str
    detail_url: str
    pdf_url: str
    pdf_sha256: str
    raw_text: str


def build_detail_page_url(official_code: str) -> str:
    """Devuelve la página HTML oficial que debe abrir el usuario."""
    code = str(official_code).strip()
    if not code.isdigit():
        raise ValueError("official_code debe ser el código numérico GVA")
    return f"{DETAIL_URL}?id_emp={code}"


def build_detail_pdf_url(official_code: str) -> str:
    """Construye la URL PDF oficial observada en páginas de detalle GVA."""
    code = str(official_code).strip()
    if not code.isdigit():
        raise ValueError("official_code debe ser el código numérico GVA")
    params = {
        f"_{PORTLET}_accion": "pdf",
        f"_{PORTLET}_codigo": code,
        "p_p_cacheability": "cacheLevelPage",
        "p_p_id": PORTLET,
        "p_p_lifecycle": "2",
        "p_p_mode": "view",
        "p_p_state": "normal",
    }
    return f"{DETAIL_URL}?{urlencode(params)}"


def _first(pattern: str, text: str, flags: int = re.IGNORECASE | re.MULTILINE) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def parse_detail_text(official_code: str, text: str, pdf_sha256: str = "") -> OpportunityDetails:
    """Extrae únicamente campos explícitos; lo no reconocible queda vacío."""
    cleaned = "\n".join(line.strip() for line in text.replace("\x00", "").splitlines())
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    code_in_text = _first(r"Código GVA\s+(\d+)", cleaned)
    if code_in_text and code_in_text != str(official_code):
        raise DetailExtractionError("El código del PDF no coincide con el código solicitado")

    organism = _first(r"^Organismo\s+(.+?)\s*$", cleaned)
    call_type = _first(r"^Convocatoria\s+(Oposición|Oferta de empleo público|Concurso(?:-oposición)?|Contratación laboral|Libre designación|Otros)\b", cleaned)
    test_type = _first(r"^Prueba\s+(Concurso-oposición|Oposición|Concurso|Prueba)\b", cleaned)
    group = _first(r"^Grupo\s+([A-Z0-9/]+)\s*$", cleaned)
    places_text = _first(r"^Número de plazas totales\s+(\d+)\s*$", cleaned)
    total_places = int(places_text) if places_text else None

    lines = [line for line in cleaned.splitlines() if line]
    title = None
    for index, line in enumerate(lines):
        if line.upper() == "GENERALITAT VALENCIANA" and index + 1 < len(lines):
            title = lines[index + 1]
            break
    if not title:
        title = next((line for line in lines if line.lower().startswith("convocatoria ") and not line.lower() == "convocatoria"), None)
    if not title:
        raise DetailExtractionError("No se pudo identificar el título del detalle oficial")
    if not code_in_text:
        raise DetailExtractionError("El PDF no contiene el campo Código GVA esperado")

    current_stage = _first(r"Etapa actual\.?\s*([^\n]+)", cleaned)
    application_opens_on = None
    application_closes_on = None
    application_status = "unknown"
    basis_index = cleaned.lower().find("bases y apertura de plazo")
    if basis_index >= 0:
        # Solo se inspecciona el tramo de la convocatoria inicial; fechas de
        # fases posteriores no deben confundirse con el plazo de solicitud.
        basis_section = cleaned[basis_index:basis_index + 5000]
        dates = re.search(
            r"Apertura plazo\s+(\d{2}/\d{2}/\d{4})"
            r".{0,160}?Cierre plazo\s+(\d{2}/\d{2}/\d{4})"
            r".{0,80}?Plazo\s+(abierto|cerrado|pendiente)",
            basis_section, re.IGNORECASE | re.DOTALL,
        )
        if dates:
            from datetime import datetime
            application_opens_on = datetime.strptime(dates.group(1), "%d/%m/%Y").date().isoformat()
            application_closes_on = datetime.strptime(dates.group(2), "%d/%m/%Y").date().isoformat()
            application_status = dates.group(3).lower()
    application_status = {"abierto": "open", "cerrado": "closed", "pendiente": "pending"}.get(application_status, application_status)


    lowered_stage = (current_stage or "").casefold()
    if any(word in lowered_stage for word in ("anul", "cancel")):
        process_status = "cancelled"
    elif "suspend" in lowered_stage:
        process_status = "suspended"
    elif current_stage:
        process_status = "in_progress"
    else:
        process_status = "unknown"

    digest = pdf_sha256 or sha256(cleaned.encode("utf-8")).hexdigest()
    return OpportunityDetails(
        official_code=str(official_code), title=title, organism=organism,
        call_type=call_type, test_type=test_type, group=group,
        total_places=total_places, current_stage=current_stage,
        application_opens_on=application_opens_on, application_closes_on=application_closes_on,
        application_status=application_status, process_status=process_status,
        detail_url=build_detail_page_url(official_code),
        pdf_url=build_detail_pdf_url(official_code),
        pdf_sha256=digest, raw_text=cleaned,
    )


def parse_detail_pdf(official_code: str, payload: bytes) -> OpportunityDetails:
    """Extrae el texto del PDF generado por Sede GVA y lo analiza."""
    if not payload.startswith(b"%PDF-"):
        raise DetailExtractionError("La respuesta de Sede GVA no es un PDF")
    try:
        reader = PdfReader(BytesIO(payload), strict=False)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise DetailExtractionError(f"No se pudo leer el PDF ({type(exc).__name__})") from None
    if not text.strip():
        raise DetailExtractionError("El PDF oficial no contiene texto extraíble")
    return parse_detail_text(official_code, text, sha256(payload).hexdigest())


def fetch_detail(opener, official_code: str) -> OpportunityDetails:
    """Descarga el PDF de detalle a través del opener configurado con Decodo."""
    url = build_detail_pdf_url(official_code)
    try:
        with opener.open(url, timeout=45) as response:
            status = getattr(response, "status", response.getcode())
            payload = response.read()
    except Exception as exc:
        raise DetailExtractionError(f"Falló la descarga del detalle ({type(exc).__name__})") from None
    if not 200 <= status < 300:
        raise DetailExtractionError(f"Sede GVA respondió HTTP {status} al solicitar el detalle")
    return parse_detail_pdf(official_code, payload)
