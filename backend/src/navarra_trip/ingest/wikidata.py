"""Enriquece los recursos con Wikidata y la entrada de Wikipedia en español.

1. Candidatos: elementos de Wikidata cerca del recurso (SPARQL `wikibase:around`).
2. Emparejado automático por nombre y distancia; `recursos_manual.csv` manda sobre él.
3. Datos del elemento (`wbgetentities`) e introducción de Wikipedia ES (`prop=extracts`).

Todo pasa por una caché en disco: la 2.ª ejecución no hace peticiones.
"""

import csv
import difflib
import json
import re
import time
import unicodedata
from pathlib import Path

import httpx

SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA = "https://www.wikidata.org/w/api.php"
WIKIPEDIA = "https://es.wikipedia.org/w/api.php"
CACHE = Path("data/raw/wikidata_cache.json")
REVISION = Path("data/wikidata_revision.csv")
MANUAL = Path(__file__).with_name("recursos_manual.csv")
UA = {"User-Agent": "navarra-trip-agent/0.1 (https://github.com/inigo99/navarra-trip-agent)"}

RADIO_KM = {"monumento": 2, "natural": 8}  # los espacios naturales son grandes
PENALIZACION_KM = {"monumento": 0.05, "natural": 0.01}
UMBRAL = 0.85  # por debajo, o si hay dos candidatos parecidos, va a revisión manual

CANDIDATOS = """SELECT ?item (SAMPLE(?lab) AS ?label)
  (GROUP_CONCAT(DISTINCT ?al; separator="|") AS ?alias) (MIN(?d) AS ?dist) WHERE {
  SERVICE wikibase:around { ?item wdt:P625 ?c .
    bd:serviceParam wikibase:center "Point(%(lon)s %(lat)s)"^^geo:wktLiteral .
    bd:serviceParam wikibase:radius "%(radio)s" . bd:serviceParam wikibase:distance ?d . }
  OPTIONAL { ?item rdfs:label ?les FILTER(lang(?les) = "es") }
  OPTIONAL { ?item rdfs:label ?lmul FILTER(lang(?lmul) = "mul") }
  OPTIONAL { ?item rdfs:label ?len FILTER(lang(?len) = "en") }
  BIND(COALESCE(?les, ?lmul, ?len) AS ?lab) FILTER(BOUND(?lab))
  OPTIONAL { ?item skos:altLabel ?al FILTER(lang(?al) = "es") }
} GROUP BY ?item"""

_STOP = {"de", "del", "la", "las", "el", "los", "y", "en", "e", "a"}


def _tokens(s: str, quitar: set[str] = frozenset()) -> list[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return [t for t in re.findall(r"[a-z0-9]+", s) if t not in _STOP and t not in quitar]


def similitud(nombre: str, municipio: str | None, etiqueta: str) -> float:
    """0-1. Se ignoran las palabras del municipio: 'La Ciudadela' ~ 'Ciudadela de Pamplona'."""
    quitar = set(_tokens(municipio or ""))
    a, b = _tokens(nombre, quitar), _tokens(etiqueta, quitar)
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    dice = 2 * len(set(a) & set(b)) / (len(set(a)) + len(set(b)))
    return max(ratio, dice)


def emparejar(recurso: dict, candidatos: list[dict]) -> tuple[str | None, list[tuple]]:
    """(qid aceptado o None, ranking [(puntuación, qid, etiqueta, km)] para revisión)."""
    pen = PENALIZACION_KM[recurso["categoria"]]
    ranking = sorted(
        (
            (
                max(similitud(recurso["nombre"], recurso["municipio"], t) for t in c["textos"])
                - c["km"] * pen,
                c["q"],
                c["textos"][0],
                c["km"],
            )
            for c in candidatos
        ),
        reverse=True,
    )
    if not ranking:
        return None, []
    unico = len(ranking) == 1 or ranking[1][0] < ranking[0][0] - 0.1
    return (ranking[0][1] if ranking[0][0] >= UMBRAL and unico else None), ranking[:3]


def leer_manual(ruta: Path = MANUAL) -> dict[str, dict]:
    """id -> {q, lon, lat, descripcion}. Vacío = se mantiene lo de la fuente."""
    with ruta.open(encoding="utf-8") as f:
        return {
            r["id"]: {
                "q": r["wikidata_id"] or None,
                "lon": float(r["lon"]) if r["lon"] else None,
                "lat": float(r["lat"]) if r["lat"] else None,
                "descripcion": r["descripcion"] or None,
            }
            for r in csv.DictReader(f)
        }


class Cliente:
    def __init__(self, client: httpx.Client | None, cache: dict, pausa: float = 1.0):
        self.client, self.cache, self.pausa = client, cache, pausa

    @staticmethod
    def clave(url: str, **params) -> str:
        return url + "?" + json.dumps(params, sort_keys=True, ensure_ascii=False)

    def get(self, url: str, **params) -> dict:
        clave = self.clave(url, **params)
        if clave not in self.cache:
            if self.client is None:
                raise KeyError(f"sin red y sin caché: {clave}")
            for intento in range(4):
                r = self.client.get(url, params=params, headers=UA)
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
                time.sleep(self.pausa * 10 * (intento + 1))  # 10, 20, 30 s
            r.raise_for_status()
            self.cache[clave] = r.json()
            time.sleep(self.pausa)  # Wikimedia pide no ir en paralelo
        return self.cache[clave]

    def candidatos(self, lon: float, lat: float, radio: float) -> list[dict]:
        q = CANDIDATOS % {"lon": lon, "lat": lat, "radio": radio}
        filas = self.get(SPARQL, query=q, format="json")["results"]["bindings"]
        return [
            {
                "q": f["item"]["value"].rsplit("/", 1)[1],
                "textos": [f["label"]["value"]] + [a for a in f["alias"]["value"].split("|") if a],
                "km": float(f["dist"]["value"]),
            }
            for f in filas
        ]

    def entidades(self, qids: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(qids), 50):
            lote = "|".join(sorted(qids[i : i + 50]))
            j = self.get(
                WIKIDATA,
                action="wbgetentities",
                ids=lote,
                format="json",
                props="descriptions|claims|sitelinks",
                languages="es",
                sitefilter="eswiki",
            )
            for q, e in j["entities"].items():
                claims = e.get("claims", {})
                coord = _valor(claims, "P625")
                imagen = _valor(claims, "P18")
                out[q] = {
                    "desc": e.get("descriptions", {}).get("es", {}).get("value"),
                    "eswiki": e.get("sitelinks", {}).get("eswiki", {}).get("title"),
                    "lon": coord and coord["longitude"],
                    "lat": coord and coord["latitude"],
                    "imagen": imagen and _commons(imagen),
                }
        return out

    def extractos(self, titulos: list[str]) -> dict[str, str]:
        out = {}
        for i in range(0, len(titulos), 20):  # exintro admite 20 páginas por petición
            j = self.get(
                WIKIPEDIA,
                action="query",
                prop="extracts",
                exintro=1,
                explaintext=1,
                redirects=1,
                format="json",
                titles="|".join(sorted(titulos[i : i + 20])),
            )
            redir = {r["to"]: r["from"] for r in j["query"].get("redirects", [])}
            for p in j["query"]["pages"].values():
                if p.get("extract"):
                    out[redir.get(p["title"], p["title"])] = p["extract"].strip()
        return out


def _valor(claims: dict, prop: str):
    try:
        return claims[prop][0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError):
        return None


def _commons(fichero: str) -> str:
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + fichero.replace(" ", "_")


def enriquecer(recursos: list[dict], cli: Cliente, manual: dict[str, dict]) -> list[list]:
    """Añade wikidata_id, descripcion, url_descripcion e imagen_url a cada recurso (in situ).

    Coordenadas: las de la revisión manual mandan; si no hay y el recurso no tiene, las de
    Wikidata. Devuelve las filas para revisión manual.
    """
    revision = []
    for r in recursos:
        if m := manual.get(r["id"]):
            r["wikidata_id"] = m["q"]
            if m["lon"] is not None:
                r["lon"], r["lat"] = m["lon"], m["lat"]
            continue
        if r["lon"] is None:
            r["wikidata_id"] = None
            revision.append([r["id"], r["nombre"], r["municipio"], "sin coordenadas", ""])
            continue
        cands = cli.candidatos(r["lon"], r["lat"], RADIO_KM[r["categoria"]])
        r["wikidata_id"], ranking = emparejar(r, cands)
        if r["wikidata_id"] is None:
            top = " | ".join(f"{q} {t} ({s:.2f}, {km:.1f} km)" for s, q, t, km in ranking)
            revision.append([r["id"], r["nombre"], r["municipio"], "dudoso", top])

    ents = cli.entidades([r["wikidata_id"] for r in recursos if r["wikidata_id"]])
    textos = cli.extractos([e["eswiki"] for e in ents.values() if e["eswiki"]])
    for r in recursos:
        e = ents.get(r["wikidata_id"] or "", {})
        texto = textos.get(e.get("eswiki") or "")
        r["descripcion"] = texto or e.get("desc")
        r["descripcion_fuente"] = "wikipedia" if texto else "wikidata" if e.get("desc") else None
        if texto:
            r["url_descripcion"] = "https://es.wikipedia.org/wiki/" + e["eswiki"].replace(" ", "_")
        elif r["wikidata_id"]:
            r["url_descripcion"] = f"https://www.wikidata.org/wiki/{r['wikidata_id']}"
        else:
            r["url_descripcion"] = None
        r["imagen_url"] = e.get("imagen")
        if r["lon"] is None and e.get("lon") is not None:
            r["lon"], r["lat"] = e["lon"], e["lat"]
        if (m := manual.get(r["id"])) and m["descripcion"]:
            r["descripcion"], r["descripcion_fuente"] = m["descripcion"], "manual"
            r["url_descripcion"] = r["url_fuente"]
    return revision


def ejecutar(recursos: list[dict], client: httpx.Client) -> None:
    """Paso de `navarra-normalizar`: caché en data/raw/ y revisión en data/wikidata_revision.csv."""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    try:
        revision = enriquecer(recursos, Cliente(client, cache), leer_manual())
    finally:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    with REVISION.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["id", "nombre", "municipio", "motivo", "candidatos"], *revision])
    con = sum(r["wikidata_id"] is not None for r in recursos)
    desc = sum(r["descripcion"] is not None for r in recursos)
    print(
        f"wikidata     {con}/{len(recursos)} enlazados | {desc} con descripción | "
        f"{len(revision)} a revisar en {REVISION}"
    )
