"""Diagnóstico aislado de proxy Decodo. No ejecuta tareas de Empleo."""
import json
import sys
from decodo_proxy import open_via_decodo


def main():
    try:
        with open_via_decodo("https://ip.decodo.com/json", timeout=30) as response:
            data = json.load(response)
            print(f"DECODO_HTTP_STATUS={response.status}")
        country = data.get("country", {})
        city = data.get("city", {})
        proxy = data.get("proxy", {})
        isp = data.get("isp", {})
        print(f"DECODO_COUNTRY={country.get('name', 'no disponible')}")
        print(f"DECODO_CITY={city.get('name', 'no disponible')}")
        print(f"DECODO_IP={proxy.get('ip', 'no disponible')}")
        print(f"DECODO_ISP={isp.get('isp', 'no disponible')}")
        print(f"DECODO_ASN={isp.get('asn', 'no disponible')}")
    except Exception as exc:
        # No imprimir mensajes de error que pudieran incluir datos sensibles.
        print(f"ERROR_DECODO={type(exc).__name__}")
        return 1

    try:
        with open_via_decodo("https://sede.gva.es/", timeout=30) as response:
            print(f"GVA_HTTP_STATUS={response.status}")
            print(f"GVA_FINAL_URL={response.geturl()}")
            print(f"GVA_ACCESS_OK={200 <= response.status < 400}")
            return 0 if 200 <= response.status < 400 else 1
    except Exception as exc:
        print(f"ERROR_GVA={type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
