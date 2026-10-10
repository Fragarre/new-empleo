"""Configuración reutilizable del proxy residencial Decodo.

Lee GVA_PROXY_URL, GVA_PROXY_USER y GVA_PROXY_PASSWORD del entorno.
No contiene credenciales y no imprime datos sensibles.
"""
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener


def build_decodo_opener():
    """Devuelve un urllib opener configurado para usar Decodo.

    GVA_PROXY_URL puede ser 'gate.decodo.com:7000' o incluir http://.
    """
    proxy_url = os.environ.get("GVA_PROXY_URL", "").strip()
    username = os.environ.get("GVA_PROXY_USER", "").strip()
    password = os.environ.get("GVA_PROXY_PASSWORD", "")

    missing = [
        name
        for name, value in (
            ("GVA_PROXY_URL", proxy_url),
            ("GVA_PROXY_USER", username),
            ("GVA_PROXY_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Faltan variables de entorno: " + ", ".join(missing))

    host = proxy_url.removeprefix("http://").removeprefix("https://").rstrip("/")
    if "/" in host or "@" in host:
        raise ValueError("GVA_PROXY_URL debe contener solo host:puerto")

    proxy = (
        "http://"
        + quote(username, safe="")
        + ":"
        + quote(password, safe="")
        + "@"
        + host
    )
    return build_opener(ProxyHandler({"http": proxy, "https": proxy}))


def open_via_decodo(url, timeout=30):
    """Abre una URL a través de Decodo, reintentando fallos transitorios."""
    last_error = None
    for attempt in range(3):
        try:
            return build_decodo_opener().open(url, timeout=timeout)
        except HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504, 522, 524) or attempt == 2:
                raise
        except (URLError, OSError) as exc:
            last_error = exc
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))
    raise last_error
