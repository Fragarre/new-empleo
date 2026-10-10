"""Diagnóstico de solo lectura del formulario oficial de búsqueda GVA.

Consulta la página pública a través de Decodo y muestra los nombres reales de
campos, formularios y scripts encontrados. No busca convocatorias ni escribe
en Supabase. No presupone nombres de parámetros.
"""
from html.parser import HTMLParser
import gzip
from urllib.parse import urljoin
import re

from decodo_proxy import open_via_decodo

URL = "https://sede.gva.es/es/cercador-ocupacio-publica"

BASE_URL = URL
PROBE_PARAMS = {
    "pruebas": "533",
    "convocatorias": "507",
    "turnos": "L",
    "tipoOrganismo": "flexRadioDefault200",
    "fechaPublicacionDesde": "2025-10-10",
    "fechaPublicacionHasta": "2026-10-10",
    "fechaPublicacionBoletinDesde": "2025-10-10",
    "fechaPublicacionBoletinHasta": "2026-10-10",
    "tamanyoPagina": "30",
}


class FormInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.scripts = []
        self._form = None
        self._select = None
        self.filter_controls = []
        self._classes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "")
        if tag in ("input", "select", "option") and any(x in classes for x in ("type-test", "shifts", "type-organism", "processing-status", "groups-list")):
            self.filter_controls.append({"tag": tag, "id": attrs.get("id", ""), "name": attrs.get("name", ""), "value": attrs.get("value", ""), "type": attrs.get("type", ""), "class": classes})
        if tag == "input" and (attrs.get("id", "").startswith("_es_gva_es_siac_portlet_SiacBuscadorEmpleoPublicoCiudadania360") or attrs.get("id", "") in ("fechaPublicacionDesde", "fechaPublicacionHasta", "fechaPublicacionBoletinDesde", "fechaPublicacionBoletinHasta")):
            self.filter_controls.append({"tag": tag, "id": attrs.get("id", ""), "name": attrs.get("name", ""), "value": attrs.get("value", ""), "type": attrs.get("type", ""), "class": classes})
        if tag == "form":
            self._form = {
                "action": attrs.get("action", ""),
                "method": attrs.get("method", "get").upper(),
                "fields": [],
            }
            self.forms.append(self._form)
        elif tag in ("input", "button", "textarea") and self._form is not None:
            self._form["fields"].append({
                "tag": tag,
                "type": attrs.get("type", ""),
                "name": attrs.get("name", ""),
                "id": attrs.get("id", ""),
                "value": attrs.get("value", ""),
            })
        elif tag == "select" and self._form is not None:
            self._select = {
                "tag": "select",
                "name": attrs.get("name", ""),
                "id": attrs.get("id", ""),
                "options": [],
            }
            self._form["fields"].append(self._select)
        elif tag == "option" and self._select is not None:
            self._select["options"].append({
                "value": attrs.get("value", ""),
                "selected": "selected" in attrs,
            })
        elif tag == "script" and attrs.get("src"):
            self.scripts.append(urljoin(URL, attrs["src"]))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "form":
            self._form = None


def main():
    # Prueba de control: distingue un fallo general del proxy de uno específico
    # del acceso a la sede GVA. No imprime credenciales ni IP de salida.
    for test_url, label in (
        ("https://example.com/", "PROXY_CONTROL"),
        (URL, "GVA_CONTROL"),
    ):
        try:
            with open_via_decodo(test_url, timeout=25) as test_response:
                print(f"{label}_STATUS={test_response.status}")
                print(f"{label}_CONTENT_TYPE={test_response.headers.get('Content-Type', '')}")
                test_response.read(256)
        except Exception as exc:
            print(f"{label}_ERROR={type(exc).__name__}: {exc}")

    with open_via_decodo(URL, timeout=45) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        html = body.decode(charset, errors="replace")
        print(f"HTTP_STATUS={response.status}")
        print(f"FINAL_URL={response.geturl()}")
        print(f"CONTENT_TYPE={response.headers.get('Content-Type', '')}")

    # Consultas de prueba de solo lectura: primero filtros base y luego fechas.
    from urllib.parse import urlencode
    probes = [
        ("BASE_FILTERS", PROBE_PARAMS),
        ("PAGE_1", {**PROBE_PARAMS, "pagina": "1"}),
        ("PAGE_2", {**PROBE_PARAMS, "pagina": "2"}),
        ("PAGE_3", {**PROBE_PARAMS, "pagina": "3"}),
        ("PAGE_4", {**PROBE_PARAMS, "pagina": "4"}),
    ]
    for label, params in probes:
        probe_url = URL + "?" + urlencode(params)
        try:
            with open_via_decodo(probe_url, timeout=45) as probe_response:
                probe_body = probe_response.read()
                probe_charset = probe_response.headers.get_content_charset() or "utf-8"
                probe_html = probe_body.decode(probe_charset, errors="replace")
                print(f"PROBE_{label}_STATUS={probe_response.status}")
                print(f"PROBE_{label}_URL={probe_response.geturl()}")
                print(f"PROBE_{label}_BYTES={len(probe_body)}")
                print(f"PROBE_{label}_TITLE_COUNT={len(re.findall(r'<title\\b', probe_html, re.I))}")
                total_match = re.search(r'class=["\'][^"\']*total-results[^"\']*["\'][^>]*>(.*?)</div>', probe_html, re.I | re.S)
                total_text = re.sub(r"<[^>]+>", " ", total_match.group(1)) if total_match else ""
                print(f"PROBE_{label}_TOTAL_RESULTS_TEXT={re.sub(r'\s+', ' ', total_text).strip()!r}")
                print(f"PROBE_{label}_ITEM_MARKERS={sum(probe_html.lower().count(x) for x in ('convocatoria', 'proceso selectivo', 'fecha de publicación'))}")
                result_pos = probe_html.lower().find('results mt-3')
                detail_hrefs = [h for h in re.findall('href="([^"]+)"', probe_html, re.I) if 'ocupacio-publica' in h.lower() or 'ocupacio_publica' in h.lower()]
                print(f"PROBE_{label}_DETAIL_HREFS_TOTAL={len(detail_hrefs)}")
                print(f"PROBE_{label}_DETAIL_HREFS_UNIQUE={len(set(detail_hrefs))}")
                print(f"PROBE_{label}_DETAIL_HREFS_SAMPLE={list(dict.fromkeys(detail_hrefs))[:40]!r}")
                employment_ids = list(dict.fromkeys(re.findall(r'id_emp=(\d+)', probe_html, re.I)))
                print(f"PROBE_{label}_EMPLOYMENT_IDS_COUNT={len(employment_ids)}")
                print(f"PROBE_{label}_EMPLOYMENT_IDS={employment_ids!r}")
                print(f"PROBE_{label}_RESULTS_CONTEXT=" + (probe_html[max(0, result_pos-300):result_pos+4500] if result_pos >= 0 else "NOT_FOUND"))
        except Exception as exc:
            print(f"PROBE_{label}_ERROR={type(exc).__name__}: {exc}")

    inspector = FormInspector()
    inspector.feed(html)
    print(f"FORM_COUNT={len(inspector.forms)}")
    for index, form in enumerate(inspector.forms, 1):
        print(f"FORM_{index}_ACTION={form['action']}")
        print(f"FORM_{index}_METHOD={form['method']}")
        for field in form["fields"]:
            if field["tag"] == "select":
                print(
                    f"SELECT name={field['name']!r} id={field['id']!r} "
                    f"options={field['options']!r}"
                )
            else:
                print(
                    f"FIELD tag={field['tag']} type={field['type']!r} "
                    f"name={field['name']!r} id={field['id']!r} "
                    f"value={field['value']!r}"
                )

    for control in inspector.filter_controls:
        print(f"FILTER_CONTROL={control!r}")
    # Relacionar cada checkbox/radio con el texto visible de su etiqueta.
    for match in re.finditer(r"<input\b[^>]*>", html, re.IGNORECASE):
        tag = match.group(0)
        typ = re.search(r'type=["\'](checkbox|radio)["\']', tag, re.IGNORECASE)
        ident = re.search(r'\bid=["\']?([^"\'\s>]+)', tag, re.IGNORECASE)
        if not typ or not ident:
            continue
        input_id = ident.group(1)
        tail = html[match.end():match.end()+900]
        label_match = re.search(r"<label\b[^>]*>.*?</label>", tail, re.IGNORECASE | re.DOTALL)
        label = re.sub(r"<[^>]+>", " ", label_match.group(0)) if label_match else ""
        label = re.sub(r"\s+", " ", label).strip()
        print(f"FILTER_OPTION id={input_id!r} type={typ.group(1)!r} label={label[:180]!r}")
    for marker in ("type-test-list", "shifts-list", "type-organism-list", "processing-status-list", "groups-list", "paginacion", "resultados", "pagination"):
        pos = html.lower().find(marker)
        print(f"HTML_MARKER_{marker}_COUNT={html.lower().count(marker)}")
        if pos >= 0:
            print(f"HTML_CONTEXT_{marker}=" + re.sub(r"\\s+", " ", html[max(0, pos-700):pos+1800]))

    relevant_scripts = [
        url for url in inspector.scripts
        if any(word in url.lower() for word in ("siac", "empleo", "buscador"))
    ]
    print(f"RELEVANT_SCRIPT_COUNT={len(relevant_scripts)}")
    for url in relevant_scripts:
        print(f"SCRIPT_URL={url}")
        try:
            with open_via_decodo(url, timeout=45) as response:
                script_body = response.read()
                # El endpoint /combo de Liferay puede devolver gzip sin que
                # urllib lo descomprima automáticamente.
                if script_body.startswith(b"\x1f\x8b"):
                    script_body = gzip.decompress(script_body)
                charset = response.headers.get_content_charset() or "utf-8"
                script = script_body.decode(charset, errors="replace")
                print(f"SCRIPT_HTTP_STATUS={response.status}")
                print(f"SCRIPT_LENGTH={len(script)}")
            print("SCRIPT_SOURCE_BEGIN")
            print(script)
            print("SCRIPT_SOURCE_END")
            terms = (
                "fechaPublicacionDesde", "fechaPublicacionHasta",
                "fechaPublicacionBoletinDesde", "fechaPublicacionBoletinHasta",
                "fechaPublicacion", "organismo", "turno", "sistemaSelectivo",
                "tipoPrueba", "tamanyoPagina", "pagina", "event_siguiente",
                "procesarUrl", "plazos", "descripcion", "cuerpo"
            )
            for term in terms:
                matches = list(re.finditer(re.escape(term), script, re.IGNORECASE))
                if matches:
                    print(f"JS_TERM={term} COUNT={len(matches)}")
                    for match in matches[:3]:
                        start = max(0, match.start() - 220)
                        end = min(len(script), match.end() + 320)
                        print("JS_CONTEXT=" + re.sub(r"\\s+", " ", script[start:end]))
        except Exception as exc:
            print(f"SCRIPT_FETCH_ERROR={type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
