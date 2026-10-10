"""Extracción auditable de convocatorias de empleo público de la GVA.
Solo consulta páginas oficiales; no escribe en Supabase.
Genera JSON y CSV con la ficha y el historial de etapas de cada resultado.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape, unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path
from pypdf import PdfReader
from urllib.parse import urlencode, urljoin, urlparse, parse_qs

from decodo_proxy import open_via_decodo

BASE = "https://sede.gva.es/es/cercador-ocupacio-publica"
DETAIL_RE = re.compile(r"/es/detall-ocupacio-publica\?[^#]*\bid_emp=(\d+)", re.I)
PARAMS = {
    "pruebas": "533",
    "convocatorias": "507",
    "turnos": "L",
    "tipoOrganismo": "GVA",
    "fechaPublicacionDesde": "2025-10-10",
    "fechaPublicacionHasta": "2026-10-10",
    "tamanyoPagina": "100",
}
# Ficha oficial detectada previamente como ausente en el listado filtrado.
SUPPLEMENTAL_IDS = ("110135", "84656")
OUT = Path("salida_gva")
TARGET_GROUPS = ("A1-01", "A2-01", "C1-01", "C2-01")
TERMINAL = (
    "nombramiento y adjudicación de destinos", "nombramiento y adjudicacion de destinos",
    "adjudicación definitiva de destinos", "adjudicacion definitiva de destinos",
    "adjudicación de destinos definitiva", "adjudicacion de destinos definitiva",
    "resolución de nombramiento", "resolucion de nombramiento",
    "finalización del proceso selectivo", "finalizacion del proceso selectivo",
    "proceso selectivo finalizado", "aprobación del expediente", "aprobacion del expediente",
    "lista de aprobados", "llista d'aprovats", "relación definitiva de personas aprobadas",
    "relacion definitiva de personas aprobadas", "resultado definitivo del proceso selectivo",
)
# Exclusiones expresas del alcance: administración general, turno libre y oposición.
# Se normalizan tildes para cubrir las variantes castellanas/valencianas.
RESTRICTED = (
    "promocion interna", "libre designacion", "acto unico telematico",
    "anuncio dificil cobertura", "acceso restringido", "fondos europeos",
    "apt-a1-", "apt-a2-", "apt-c1-", "apt-c2-",
    "cuerpo de inspectores de tributos", "agencia tributaria", "tributaria",
    "intervencion general", "interventor", "abogacia", "abogado del estado",
    "personal estatutario", "sanidad", "educacion", "policia", "bomberos",
    "bolsa de empleo", "bolsa de trabajo", "formacion de bolsa",
    "libre nombramiento", "comision de servicios", "concurso de traslados",
    "concurso general", "concurso especifico", "concurso-oposicion", "concurso oposicion",
    "sistema de concurso", "sistema de meritos",
)


def normalize_match(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).lower()



def classify_scope(title, body_text="", link_text=""):
    """Devuelve (incluible, motivo) aplicando los filtros expresos del proyecto."""
    combined = normalize_match(" ".join((title, body_text, link_text)))
    # Las exclusiones se buscan en el título identificativo y el texto del enlace,
    # no en todo el cuerpo: las fichas pueden mencionar otros turnos como contexto.
    identity = normalize_match(" ".join((title, link_text)))
    restricted_term = next((term for term in RESTRICTED if term in identity), None)
    if restricted_term:
        return False, f"exclusion_explicita:{restricted_term}"
    has_target_group = bool(re.search(r"\b(?:A1-01|A2-01|C1-01|C2-01)\b", combined, re.I))
    has_admin_title = bool(re.search(
        r"\b(?:cuerpo|escala|agrupacion)\s+administrativ[oa]s?\b|"
        r"\bauxiliar(?:es)? administrativ[oa]s?\b|\bcuerpo administrativo\b",
        combined, re.I))
    if not (has_target_group or has_admin_title):
        return False, "fuera_de_grupos_objetivo"
    if not re.search(r"\b(?:turno libre|torn lliure)\b", combined):
        return False, "sin_evidencia_turno_libre"
    if not re.search(r"\b(?:oposicion|oposiciones|proceso selectivo|pruebas selectivas)\b", combined):
        return False, "sin_evidencia_oposicion"
    return True, "alcance_confirmado"



class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._link_text = []
        self.text_parts = []
        self._skip = 0
        self._title = False
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "a" and a.get("href"):
            self._href = a["href"]
            self._link_text = []
        if tag == "title":
            self._title = True
        if tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "section"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "a" and self._href is not None:
            self.links.append((self._href, clean(" ".join(self._link_text))))
            self._href = None
            self._link_text = []
        if tag == "title":
            self._title = False
        if tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "section"):
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        val = unescape(data)
        self.text_parts.append(val)
        if self._href is not None:
            self._link_text.append(val)
        if self._title:
            self.title_parts.append(val)

    @property
    def text(self):
        return clean(" ".join(self.text_parts))

    @property
    def title(self):
        return clean(" ".join(self.title_parts))


def clean(value):
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def fetch(url, timeout=18):
    last = None
    for attempt in range(3):
        try:
            with open_via_decodo(url, timeout=timeout) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                return body.decode(charset, errors="replace"), response.geturl()
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"No se pudo descargar {url}: {type(last).__name__}: {last}")


def select_application_pair(publication_text, date_pairs, preferred_pairs=None):
    """Elige el plazo inicial próximo a la publicación de las bases.

    La sede puede publicar la apertura 1-2 semanas después de la publicación
    en DOGV. Limitar la búsqueda a 7 días descartaba plazos válidos.
    """
    if not publication_text:
        return None
    try:
        publication_date = datetime.strptime(publication_text, "%d/%m/%Y").date()
    except ValueError:
        return None
    # Si el plazo aparece junto a la etiqueta de las bases, prevalece sobre
    # fechas similares que la sede repite en enlaces o ayudas generales.
    preferred_pairs = list(preferred_pairs or [])
    candidate_pool = preferred_pairs if preferred_pairs else date_pairs
    candidates = []
    for candidate_pair in candidate_pool:
        try:
            opening_date = datetime.strptime(candidate_pair.group(1), "%d/%m/%Y").date()
            closing_date = datetime.strptime(candidate_pair.group(2), "%d/%m/%Y").date()
        except ValueError:
            continue
        day_gap = (opening_date - publication_date).days
        if 0 <= day_gap <= 30 and closing_date >= opening_date:
            candidates.append((abs(day_gap - 1), candidate_pair))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def is_terminal_stage(current):
    current_normalized = (current or "").lower()
    stage_annuls_only_an_act = "anulación acto" in current_normalized or "anulacion acto" in current_normalized
    terminal = (
        any(x in current_normalized for x in TERMINAL)
        or "adjudicación de destinos y fecha de cese/toma de posesión" in current_normalized
        or "adjudicacion de destinos y fecha de cese/toma de posesion" in current_normalized
    )
    return terminal and not stage_annuls_only_an_act


def fetch_official_pdf(emp_id):
    """Descarga y extrae el PDF oficial de la ficha completa GVA."""
    params = {
        "_es_gva_es_siac_portlet_SiacDetalleEmpleoPublicoNuevoGVA_accion": "pdf",
        "_es_gva_es_siac_portlet_SiacDetalleEmpleoPublicoNuevoGVA_codigo": str(emp_id),
        "p_p_cacheability": "cacheLevelPage",
        "p_p_id": "es_gva_es_siac_portlet_SiacDetalleEmpleoPublicoNuevoGVA",
        "p_p_lifecycle": "2",
        "p_p_mode": "view",
        "p_p_state": "normal",
    }
    url = "https://sede.gva.es/es/detall-ocupacio-publica?" + urlencode(params)
    try:
        with open_via_decodo(url, timeout=12) as response:
            data = response.read()
        if not data.startswith(b"%PDF"):
            return url, "", "La descarga oficial no devolvió un PDF"
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return url, clean(text), ""
    except Exception as exc:
        return url, "", f"{type(exc).__name__}: {exc}"


def is_service_unavailable_page(html, title=""):
    """Detecta páginas de error de la sede antes de tratar sus enlaces como fichas."""
    title_text = clean(title).lower()
    parser = PageParser()
    parser.feed(html or "")
    body_text = parser.text.lower()
    signals = (
        "aplicación fuera de servicio", "aplicacion fuera de servicio",
        "servicio temporalmente no disponible", "service unavailable",
        "temporarily unavailable",
    )
    return any(signal in title_text or signal in body_text for signal in signals)


def extract_result_links(html):
    parser = PageParser()
    parser.feed(html)
    found = {}
    candidates = list(parser.links)
    # Respaldo por regex sobre el HTML original por si Liferay altera el marcado.
    candidates.extend((unescape(href), "") for href in re.findall(
        r"""href=["']([^"']*detall-ocupacio-publica[^"']*)["']""",
        html, re.I))
    for href, label in candidates:
        href = unescape(href)
        if "/detall-ocupacio-publica" not in href or "id_emp=" not in href:
            continue
        absolute = urljoin(BASE, href)
        match = DETAIL_RE.search(absolute) or re.search(r"[?&]id_emp=(\d+)", absolute, re.I)
        if not match:
            continue
        emp_id = match.group(1)
        found[emp_id] = {"id_emp": emp_id, "url": f"https://sede.gva.es/es/detall-ocupacio-publica?id_emp={emp_id}", "link_text": label}
    return list(found.values()), parser.text


def parse_detail(emp):
    html, final_url = fetch(emp["url"], timeout=10)
    # Validar también cada ficha individual: la sede puede devolver su página
    # de error con HTTP 200 y un título aparentemente procesable.
    title_match = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.I | re.S)
    page_title = clean(html_unescape(re.sub(r"<[^>]+>", " ", title_match.group(1)))) if title_match else ""
    if is_service_unavailable_page(html, page_title):
        raise RuntimeError(
            f"La ficha individual {emp.get('id_emp', '')} devolvió una página "
            "de servicio no disponible; se descarta para evitar una ficha falsa."
        )
    # Para la ficha usamos el HTML visible, quitando script/style antes de
    # eliminar etiquetas. El parser de enlaces no siempre recorre bien el DOM
    # dinámico de Liferay hasta el bloque final de etapas.
    visible_html = re.sub(r"<(script|style|noscript)\b[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = clean(html_unescape(re.sub(r"<[^>]+>", " ", visible_html)))
    title_match = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.I | re.S)
    title = clean(html_unescape(re.sub(r"<[^>]+>", " ", title_match.group(1)))) if title_match else ""
    heading_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", visible_html, re.I | re.S)
    record_title = clean(html_unescape(re.sub(r"<[^>]+>", " ", heading_match.group(1)))) if heading_match else ""
    # Extrae texto tras etiquetas conocidas sin depender de un diseño CSS concreto.
    def after(label, max_len=500):
        m = re.search(re.escape(label) + r"\s*[:：]?\s*(.{1," + str(max_len) + r"}?)(?=\s+(?:Código SIA|Codi SIA|Código GVA|Codi GVA|INFORMACIÓN BÁSICA|INFORMACIÓ BÀSICA|LISTADO DE ETAPAS|Llistat d'etapes|AYUDA|AJUDA)\b|$)", text, re.I)
        return clean(m.group(1)) if m else ""

    current = after("Etapa actual", 300) or after("Etapa actual", 300)
    # Título principal visible en el cuerpo, preferible al title genérico de la sede.
    body_title = ""
    for pattern in (r"Detall ocupació pública\s+(.{5,260}?)\s+(?:Conselleria|Organisme|Organismo)\b",
                    r"Detalle empleo público\s+(.{5,260}?)\s+(?:Conselleria|Organismo)\b"):
        m = re.search(pattern, text, re.I)
        if m:
            body_title = clean(m.group(1))
            break
    # Preferir el título de la convocatoria visible en el cuerpo de la ficha,
    # ya que el H1 puede ser solo el rótulo genérico "Detalle empleo público".
    conv_title_match = re.search(
        r"\b(Convocatoria\s+\d+\s*/\s*\d+.*?)(?=\s+-\s*Sede Electrónica|\s+Navegación|\s+(?:Conselleria|Organismo|Organisme)\b|$)",
        text, re.I)
    if conv_title_match:
        title = clean(conv_title_match.group(1))
    elif record_title and record_title.lower() not in ("detalle empleo público", "detall ocupació pública", "detalle", "detall"):
        title = record_title
    else:
        title = body_title or title
    gva_code = ""
    m = re.search(r"(?:Código|Codi) GVA\s*(\d+)", text, re.I)
    if m:
        gva_code = m.group(1)
    if not gva_code:
        gva_code = emp["id_emp"]
    sia_code = ""
    m = re.search(r"(?:Código|Codi) SIA\s*(\d+)", text, re.I)
    if m:
        sia_code = m.group(1)

    # Captura el bloque de plazas y toda la secuencia de etapas publicada.
    places_total = ""
    m = re.search(r"(?:Número de plazas totales|Núm\. de places totals|Plazas totales)\s*[:]?\s*([\d.]+)", text, re.I)
    if not m:
        m = re.search(r"Plazas\s+([\d.]+)", text, re.I)
    if m:
        places_total = m.group(1).replace(".", "")
    dist = {}
    for label, value in re.findall(r"(Libre general|Turno libre|Torn lliure|Promoción interna|Promoció interna|Discapacidad intelectual|Diversidad funcional|Discapacidad|Enfermedad mental)\s*:?\s*([\d.]+)", text, re.I):
        dist[label.lower()] = value.replace(".", "")
    stages_section = ""
    # La etiqueta aparece también en la navegación lateral; tomar la última
    # aparición para recuperar el bloque real del historial de etapas.
    stage_labels = list(re.finditer(r"LISTADO DE ETAPAS|Llistat d'etapes", text, re.I))
    if stage_labels:
        start = stage_labels[-1].end()
        tail = text[start:]
        # No cortar en "AYUDA": ese enlace aparece en la navegación antes del
        # historial real. Conservar el bloque completo y limitarlo defensivamente.
        stages_section = clean(tail[:12000])
    application_window = ""
    application_start = ""
    application_end = ""
    application_match = re.search(
        r"(?:Plazo de solicitud|Plazo de presentación de solicitudes|Presentación de solicitudes|Presentació de sol\.licituds|Termini de sol\.licitud)(.{0,500})",
        stages_section or text, re.I)
    if application_match:
        application_window = clean(application_match.group(0))[:600]
        app_dates = re.findall(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b", application_window)
        if len(app_dates) >= 2:
            application_start, application_end = app_dates[0], app_dates[1]
    # Fecha de plazo de solicitud: el buscador puede mostrar una etapa intermedia; no se confunde con el plazo inicial.
    dates = re.findall(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b", text)
    norm = text.lower()
    body_start = text.rfind("Detalle empleo público")
    body_main = text[body_start:body_start + 6000] if body_start >= 0 else text[:6000]
    classification_text = title + " " + body_main[:6000]
    in_scope, scope_reason = classify_scope(
        title, text, emp.get("link_text", "")
    )
    restricted = not in_scope
    group_match = re.search(r"\b(A1-01|A2-01|C1-01|C2-01)\b", normalize_match(classification_text), re.I)
    group = group_match.group(1).upper() if group_match else ""
    administrative = bool(re.search(
        r"\b(?:cuerpo|escala|agrupacion)\s+administrativ[oa]s?\b|"
        r"\bauxiliar(?:es)? administrativ[oa]s?\b|\bcuerpo administrativo\b",
        normalize_match(classification_text), re.I))
    target = in_scope
    pdf_url = ""
    ficha_pdf_texto = ""
    pdf_error = ""
    fecha_publicacion = ""
    if target:
        # No conservar fechas capturadas de etapas ajenas al plazo inicial.
        # Solo se rellenan si se identifican en el bloque oficial de las bases.
        application_start, application_end, application_window = "", "", ""
        pdf_url, ficha_pdf_texto, pdf_error = fetch_official_pdf(emp["id_emp"])
        if ficha_pdf_texto:
            stages_pdf_match = re.search(
                r"(?:LISTADO DE ETAPAS|Llistat d'etapes)(.*?)(?:AYUDA|AJUDA|Preguntas frecuentes|Preguntes freqüents|Enlaces de interés|Enllaços d'interés|$)",
                ficha_pdf_texto, re.I)
            if stages_pdf_match:
                stages_section = clean(stages_pdf_match.group(1))
            else:
                stages_section = ficha_pdf_texto
            # El plazo inicial suele figurar en "Bases y apertura de plazo".
            # Si hubo modificación, usar el primer par apertura/cierre del bloque.
            base_labels = list(re.finditer(r"Bases y apertura de plazo", ficha_pdf_texto, re.I))
            if base_labels:
                base_tail = ficha_pdf_texto[base_labels[-1].start():]
                date_pattern = r"Apertura plazo\s+(\d{2}/\d{2}/\d{4})\s+Cierre plazo\s+(\d{2}/\d{2}/\d{4})"
                date_pairs = list(re.finditer(date_pattern, base_tail, re.I))
                # La ficha puede repetir plazos genéricos en el pie de página.
                # Dar prioridad al par situado inmediatamente junto a las bases.
                preferred_pairs = list(re.finditer(date_pattern, base_tail[:250], re.I))
                publication_match = re.search(
                    r"Publicación.*?\bde\s+(\d{2}/\d{2}/\d{4})",
                    base_tail, re.I)
                selected_pair = None
                if publication_match:
                    fecha_publicacion = datetime.strptime(publication_match.group(1), "%d/%m/%Y").date().isoformat()
                if publication_match and date_pairs:
                    selected_pair = select_application_pair(publication_match.group(1), date_pairs, preferred_pairs)
                if selected_pair:
                    application_start, application_end = selected_pair.group(1), selected_pair.group(2)
                    application_window = clean(selected_pair.group(0))
                else:
                    term_match = re.search(
                        r"Plazo Especificación del plazo(.{0,700}?)(?=Forma de presentación|Formularios y documentación|$)",
                        base_tail, re.I)
                    if term_match:
                        application_window = clean(term_match.group(0))[:800]
                    else:
                        application_window = clean(base_tail[:800])
    terminal = is_terminal_stage(current)
    # Si ya hay un resultado publicado, la oportunidad deja de ser activa según el criterio del proyecto.
    status = "FINALIZADA_PROBABLE" if terminal else ("EN_SEGUIMIENTO" if current else "REVISAR_ETAPA")
    en_plazo_inscripcion = None
    if application_start and application_end:
        try:
            today = datetime.now().date()
            start_date = datetime.strptime(application_start, "%d/%m/%Y").date()
            end_date = datetime.strptime(application_end, "%d/%m/%Y").date()
            en_plazo_inscripcion = start_date <= today <= end_date
        except ValueError:
            en_plazo_inscripcion = None
    return {
        **emp,
        "url_final": final_url,
        "titulo": title,
        "codigo_gva": gva_code,
        "codigo_sia": sia_code,
        "grupo_objetivo": group,
        "administrativo_por_titulo": administrative,
        "candidata_por_criterios_basicos": target,
        "convocatoria_tipo_restringido_detectado": restricted,
        "motivo_clasificacion_alcance": scope_reason,
        "etapa_actual": current,
        "plazas_totales": places_total,
        "distribucion_plazas": dist,
        "fecha_publicacion": fecha_publicacion,
        "fechas_detectadas": list(dict.fromkeys(dates)),
        "plazo_solicitud_texto": application_window,
        "plazo_solicitud_inicio": application_start,
        "plazo_solicitud_fin": application_end,
        "en_plazo_inscripcion": en_plazo_inscripcion,
        "etapas_completas_texto": stages_section,
        "ficha_pdf_url": pdf_url,
        "ficha_completa_texto": ficha_pdf_texto,
        "error_pdf": pdf_error,
        "oportunidad_en_seguimiento": bool(target and status != "FINALIZADA_PROBABLE"),
        "estado_provisional": status,
        "requiere_revision": bool(target and (not ficha_pdf_texto or not stages_section or not application_start or not application_end)) or not bool(current and places_total),
        "error": "",
    }


# La salida mantiene fechas UTC conscientes de zona horaria para auditoría.
def main():
    OUT.mkdir(exist_ok=True)
    first_url = BASE + "?" + urlencode(PARAMS)
    html, final_list_url = fetch(first_url, timeout=25)
    first, list_text = extract_result_links(html)
    # Añadir fichas conocidas que el buscador no incluyó pese a cumplir alcance.
    present_ids = {x["id_emp"] for x in first}
    for supplemental_id in SUPPLEMENTAL_IDS:
        if supplemental_id not in present_ids:
            first.append({"id_emp": supplemental_id, "url": f"https://sede.gva.es/es/detall-ocupacio-publica?id_emp={supplemental_id}", "link_text": "Ficha complementaria identificada en auditoría"})
    print(f"LISTADO_URL_FINAL={final_list_url}")
    print(f"LISTADO_HTML_CARACTERES={len(html)}")
    title_match = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.I | re.S)
    list_title = clean(re.sub(r'<[^>]+>', ' ', title_match.group(1))) if title_match else ""
    print(f"LISTADO_TITULO={list_title!r}")
    if is_service_unavailable_page(html, list_title):
        raise RuntimeError(
            "La sede GVA devolvió una página de servicio no disponible; "
            "se aborta para evitar publicar una extracción falsa."
        )
    print(f"LISTADO_ENLACES_FICHA_INICIALES={len(first)}")
    all_items = {x["id_emp"]: x for x in first}
    if not all_items:
        raise RuntimeError("El buscador devolvió cero fichas; no se generará un resultado vacío. Revisar respuesta/proxy/HTML.")
    # Comprobar siempre la paginación: el HTML puede contener menos de 100
    # enlaces aunque el listado tenga más resultados (enlaces no reconocidos,
    # fichas complementarias o cambios en el marcado de la sede).
    for page in range(2, 21):
        page_url = BASE + "?" + urlencode({**PARAMS, "pagina": str(page)})
        page_html, _ = fetch(page_url, timeout=25)
        if is_service_unavailable_page(page_html):
            raise RuntimeError(f"La sede devolvió un error al consultar la página {page} del listado.")
        items, _ = extract_result_links(page_html)
        fresh = [x for x in items if x["id_emp"] not in all_items]
        print(f"LISTADO_PAGINA={page} enlaces={len(items)} nuevos={len(fresh)}")
        if not items or not fresh:
            break
        all_items.update({x["id_emp"]: x for x in fresh})

    print(f"RESULTADOS_UNICOS_LISTADO={len(all_items)}")
    rows = []
    failures = []
    # Concurrencia moderada para no sobrecargar la sede ni el proxy.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(parse_detail, item): item for item in all_items.values()}
        for i, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {**item, "url_final": "", "titulo": item.get("link_text", ""),
                       "codigo_gva": item["id_emp"], "codigo_sia": "", "grupo_objetivo": "",
                       "administrativo_por_titulo": False, "candidata_por_criterios_basicos": False,
                       "convocatoria_tipo_restringido_detectado": False, "etapa_actual": "",
                       "plazas_totales": "", "distribucion_plazas": {}, "fechas_detectadas": [],
                       "etapas_completas_texto": "", "estado_provisional": "ERROR_DESCARGA",
                       "requiere_revision": True, "error": f"{type(exc).__name__}: {exc}"}
                failures.append(item["id_emp"])
            rows.append(row)
            if i % 10 == 0 or i == len(all_items):
                print(f"FICHAS_PROCESADAS={i}/{len(all_items)} errores={len(failures)}")

    rows.sort(key=lambda x: (not x.get("candidata_por_criterios_basicos", False), x.get("grupo_objetivo", ""), x.get("titulo", "")))
    json_path = OUT / "convocatorias_gva.json"
    csv_path = OUT / "convocatorias_gva.csv"
    json_path.write_text(json.dumps({
        "fecha_extraccion_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "filtros": PARAMS,
        "total_resultados_listado": len(all_items),
        "total_fichas_con_error": len(failures),
        "errores_ids": failures,
        "convocatorias": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    columns = ["id_emp", "url", "titulo", "codigo_gva", "codigo_sia", "grupo_objetivo",
               "administrativo_por_titulo", "candidata_por_criterios_basicos",
               "convocatoria_tipo_restringido_detectado", "motivo_clasificacion_alcance", "etapa_actual", "plazas_totales",
               "distribucion_plazas", "fecha_publicacion", "fechas_detectadas", "plazo_solicitud_texto",
               "plazo_solicitud_inicio", "plazo_solicitud_fin", "en_plazo_inscripcion",
               "estado_provisional", "oportunidad_en_seguimiento", "requiere_revision", "etapas_completas_texto",
               "ficha_pdf_url", "ficha_completa_texto", "error_pdf", "error"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            serial = dict(row)
            for k in ("distribucion_plazas", "fechas_detectadas"):
                serial[k] = json.dumps(serial.get(k, {} if k == "distribucion_plazas" else []), ensure_ascii=False)
            writer.writerow(serial)
    candidates = [r for r in rows if r.get("candidata_por_criterios_basicos")]
    needs_review = sum(bool(r.get("requiere_revision")) for r in rows)
    print(f"FICHAS_CANDIDATAS_PRELIMINARES={len(candidates)}")
    print(f"FICHAS_REQUIEREN_REVISION={needs_review}")
    print(f"FICHAS_CON_ERROR={len(failures)}")
    print(f"JSON={json_path}")
    print(f"CSV={csv_path}")
    # No ocultar una extracción incompleta como si fuera éxito.
    if failures or not rows:
        print("RESULTADO=INCOMPLETO; revisar errores y volver a ejecutar")
        sys.exit(2)
    print("RESULTADO=EXTRACCION_COMPLETADA; clasificación y cifras siguen siendo provisionales")


if __name__ == "__main__":
    main()
