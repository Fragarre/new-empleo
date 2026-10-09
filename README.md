# new-emplep

Sistema dedicado exclusivamente al descubrimiento y seguimiento de oportunidades de empleo público de la Generalitat Valenciana (GVA), a partir de fuentes oficiales.

## Estado actual

- Integración de proxy Decodo en `decodo_proxy.py`.
- Descubrimiento inicial desde el [Buscador oficial de empleo público GVA](https://sede.gva.es/es/cercador-ocupacio-publica).
- Extracción de metadatos explícitos de los PDF oficiales de detalle.
- Esquema SQLite local con observaciones e historial de cambios.
- Pruebas automáticas con GitHub Actions.

## Configuración del proxy

Variables de entorno requeridas en el servicio de ejecución:

- `GVA_PROXY_URL`: endpoint, por ejemplo `gate.decodo.com:7000`.
- `GVA_PROXY_USER`: usuario de Decodo con la selección de país/ASN/sesión deseada.
- `GVA_PROXY_PASSWORD`: contraseña de Decodo.

No guardar credenciales en el repositorio.

## Diagnósticos y pruebas

Diagnóstico de conectividad: `python diagnostico_decodo.py`.

Inspección de la respuesta HTML del buscador: `python probe_sede_gva.py`.

Pruebas unitarias: `python -m pip install -r requirements.txt` y después `python -m unittest discover -s tests -v`.

## Persistencia local

Inicializar el esquema local con:

```python
from gva_oportunidades.db import initialize_database
initialize_database()
```

La ruta por defecto es `data/oportunidades_gva.sqlite3`. La creación del esquema no descarga ni modifica datos reales de empleo.

## Validación pendiente

El test de conectividad desde Render confirmó HTTP 200 en el endpoint de IP y en la Sede GVA. La lectura de campos en textos de ejemplo y las pruebas de persistencia se verifican por CI. Aún falta comprobar el HTML real del buscador desde Render y contrastar la extracción de detalles contra una muestra real antes de dar por completo el descubrimiento.
