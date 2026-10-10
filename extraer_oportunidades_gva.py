"""Extracción auditable de convocatorias de empleo público de la GVA.
Solo consulta páginas oficiales; no escribe en Supabase.
Genera JSON y CSV con la ficha y el historial de etapas de cada resultado.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
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
OUT = Path("salida_gva")
TARGET_GROUPS = ("A1-01", "A2-01", "C1-01", "C2-01")
TERMINAL = (
    "nombramiento y adjudicación de destinos", "nombramiento y adjudicacion de destinos",
    "adjudicación definitiva de destinos", "adjudicacion definitiva de destinos",
    "adjudicación de destinos definitiva", "adjudicacion de destinos definitiva",
    "resolución de nombramiento", "resolucion de nombramiento",
    "finalización del proceso selectivo", "finalizacion del proceso selectivo",
    "proceso selectivo finalizado", "aprobación del expediente", "aprobacion del expediente",
)
RESTRICTED = ("promoción interna", "promocion interna", "libre designación", "libre designacion",
              "acto único telemático", "acto unico telematico", "anuncio difícil cobertura",
              "anuncio dificil cobertura", "acceso restringido")


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


def extract_result_links(html):
    parser = PageParser()
    parser.feed(html)
    found = {}
    candidates = list(parser.links)
    # Respaldo por regex sobre el HTML original por si Liferay altera el marcado.
    candidates.extend((unescape(href), "") for href in re.findall(
        r"""href=["']([^"']*detall-ocupacio-publica[^"']*id_emp=\d+[^"']*)["']""",
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
    html, final_url = fetch(emp["url"], timeout=18)
    p = PageParser()
    p.feed(html)
    text = p.text
    title = p.title
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
    title = body_title or title
    gva_code = ""
    m = re.search(r"(?:Código|Codi) GVA\s*(\d+)", text, re.I)
    if m:
        gva_code = m.group(1)
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
    m = re.search(r"(?:LISTADO DE ETAPAS|Llistat d'etapes)(.*?)(?:AYUDA|AJUDA|Preguntas frecuentes|Preguntes freqüents|Enlaces de interés|Enllaços d'interés|$)", text, re.I)
    if m:
        stages_section = clean(m.group(1))
    # Fecha de plazo de solicitud: el buscador puede mostrar una etapa intermedia; no se confunde con el plazo inicial.
    dates = re.findall(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b", text)
    norm = text.lower()
    restricted = any(x in norm for x in RESTRICTED)
    group_match = re.search(r"\b(A1-01|A2-01|C1-01|C2-01)\b", text, re.I)
    group = group_match.group(1).upper() if group_match else ""
    administrative = bool(re.search(r"administrativ[oa]|auxiliar administrativo|cuerpo superior de administración|cos superior d'administració", norm, re.I))
    target = bool(group or administrative) and not restricted
    terminal = any(x in norm for x in TERMINAL)
    # No se afirma que el proceso esté activo si la etapa no se ha podido extraer.
    status = "FINALIZADA_PROBABLE" if terminal else ("EN_SEGUIMIENTO" if current else "REVISAR_ETAPA")
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
        "etapa_actual": current,
        "plazas_totales": places_total,
        "distribucion_plazas": dist,
        "fechas_detectadas": list(dict.fromkeys(dates)),
        "etapas_completas_texto": stages_section,
        "estado_provisional": status,
        "requiere_revision": not bool(current and places_total and stages_section),
        "error": "",
    }


def main():
    OUT.mkdir(exist_ok=True)
    first_url = BASE + "?" + urlencode(PARAMS)
    html, final_list_url = fetch(first_url, timeout=25)
    first, list_text = extract_result_links(html)
    print(f"LISTADO_URL_FINAL={final_list_url}")
    print(f"LISTADO_HTML_CARACTERES={len(html)}")
    title_match = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.I | re.S)
    print(f"LISTADO_TITULO={clean(re.sub(r'<[^>]+>', ' ', title_match.group(1))) if title_match else ''!r}")
    print(f"LISTADO_ENLACES_FICHA_INICIALES={len(first)}")
    all_items = {x["id_emp"]: x for x in first}
    if not all_items:
        raise RuntimeError("El buscador devolvió cero fichas; no se generará un resultado vacío. Revisar respuesta/proxy/HTML.")
    # Solo consultar más páginas si se alcanza el límite de 100 resultados.
    for page in range(2, 21):
        if len(all_items) < 100:
            break
        page_url = BASE + "?" + urlencode({**PARAMS, "pagina": str(page)})
        page_html, _ = fetch(page_url, timeout=25)
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
    with ThreadPoolExecutor(max_workers=4) as pool:
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
        "fecha_extraccion_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "filtros": PARAMS,
        "total_resultados_listado": len(all_items),
        "total_fichas_con_error": len(failures),
        "errores_ids": failures,
        "convocatorias": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    columns = ["id_emp", "url", "titulo", "codigo_gva", "codigo_sia", "grupo_objetivo",
               "administrativo_por_titulo", "candidata_por_criterios_basicos",
               "convocatoria_tipo_restringido_detectado", "etapa_actual", "plazas_totales",
               "distribucion_plazas", "fechas_detectadas", "estado_provisional",
               "requiere_revision", "etapas_completas_texto", "error"]
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
