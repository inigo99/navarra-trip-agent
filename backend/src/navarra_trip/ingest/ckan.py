"""Descarga en crudo los conjuntos de la v1 desde la API CKAN de datosabiertos.navarra.es."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

API = "https://datosabiertos.navarra.es/api/3/action"
CONJUNTOS = [
    "arte-y-monumentos",
    "espacios-naturales",
    "alojamientos-inscritos-en-el-registro-de-turismo-de-navarra",
    "restaurantes-inscritos-en-el-registro-de-turismo-de-navarra",
    "agroturismos-en-activo-del-registro-de-turismo-de-navarra",
    "empresas-de-actividades-inscritas-en-el-registro-de-turismo-de-navarra",
    "turismo-ornitol-gico",
    "recursos-turisticos",  # afluencia mensual de visitantes
]
# Oficinas de turismo: no están en el datastore de CKAN, se piden al WFS de IDENA
OFICINAS = "oficinas-de-turismo"
WFS_OFICINAS = (
    "https://idena.navarra.es/ogc/wfs?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=IDENA:DOTACI_Sym_OfiTurismo&outputFormat=application/json&srsName=EPSG:4326"
)
RAW = Path("data/raw")


def _get(client: httpx.Client, action: str, **params) -> dict:
    r = client.get(f"{API}/{action}", params=params)
    r.raise_for_status()
    body = r.json()
    if not body["success"]:
        raise RuntimeError(f"CKAN {action}: {body.get('error')}")
    return body["result"]


def descargar(client: httpx.Client, nombre: str) -> dict:
    """Metadatos del conjunto + todos los registros de su recurso en el datastore."""
    pkg = _get(client, "package_show", id=nombre)
    res = next(r for r in pkg["resources"] if r.get("datastore_active"))
    registros: list[dict] = []
    while True:
        page = _get(
            client, "datastore_search", resource_id=res["id"], limit=1000, offset=len(registros)
        )
        registros += page["records"]
        if not page["records"] or len(registros) >= page["total"]:
            break
    return {
        "conjunto": nombre,
        "titulo": pkg["title"],
        "licencia": pkg["license_id"],
        "url_fuente": f"https://datosabiertos.navarra.es/es/dataset/{nombre}",
        "recurso_id": res["id"],
        "actualizado": res.get("last_modified"),
        "spatial": pkg.get("spatial"),
        "descargado": datetime.now(UTC).isoformat(timespec="seconds"),
        "registros": registros,
    }


def main(argv: list[str] | None = None) -> None:
    """`navarra-descargar [--force]`: no vuelve a descargar lo que ya está en data/raw/."""
    force = "--force" in (sys.argv[1:] if argv is None else argv)
    RAW.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, headers={"User-Agent": "navarra-trip-agent"}) as client:
        for nombre in CONJUNTOS:
            destino = RAW / f"{nombre}.json"
            if destino.exists() and not force:
                print(f"en caché  {nombre}")
                continue
            datos = descargar(client, nombre)
            destino.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{len(datos['registros']):>6}  {nombre}")
        destino = RAW / f"{OFICINAS}.json"
        if force or not destino.exists():
            datos = descargar_oficinas(client)
            destino.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{len(datos['registros']):>6}  {OFICINAS}")


def descargar_oficinas(client: httpx.Client) -> dict:
    r = client.get(WFS_OFICINAS)
    r.raise_for_status()
    return {
        "conjunto": OFICINAS,
        "titulo": "Información turística. Oficinas de turismo",
        "licencia": "CC-BY-4.0",
        "url_fuente": "https://datosabiertos.navarra.es/es/dataset/spasitnadotaci_sym_ofiturismo-xml",
        "descargado": datetime.now(UTC).isoformat(timespec="seconds"),
        "registros": r.json()["features"],
    }
