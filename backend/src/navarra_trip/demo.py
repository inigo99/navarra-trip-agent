"""`navarra-demo`: planes de ejemplo para la web pública, que es estática (sin servidor).

Se generan en local (OSRM y el LLM de NAVARRA_LLM: Ollama o Groq) y se escriben en
web/public/ejemplos: un JSON y un GPX por plan, las fichas de sus lugares (recursos.json) y el
índice. La web con NEXT_PUBLIC_DEMO=1 los
lee en vez de llamar a la API.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from navarra_trip import agente, api, planner
from navarra_trip.tools import consultas

DESTINO = Path("../web/public/ejemplos")  # desde backend/
EJEMPLOS = {
    "estella-romanico": "3 días en Estella, románico y naturaleza",
    "olite-castillos-vino": "2 días en Olite, castillos y vino",
    "pamplona-pintxos": "Un fin de semana en Pamplona a pie, museos y ronda de pintxos",
    "ochagavia-montes": "3 días desde Ochagavía, senderismo y cimas, ritmo intenso",
    "sanguesa-foces": "2 días en Sangüesa, foces y monasterios",
    "tudela-bardenas": "2 days in Tudela, Bardenas Reales and local food",
    "elizondo-baztan": "Deux jours tranquilles à Elizondo, vallée du Baztan, nature et villages",
    # peticiones de opciones: lista sin itinerario
    "isaba-rutas": "Quiero hacer una ruta de monte cerca de Isaba",
    "estella-pintxos": "Dime bares de pintxos en Estella",
}


def exportar(grafo, con, ejemplos: dict[str, str], destino: Path = DESTINO) -> list[dict]:
    destino.mkdir(parents=True, exist_ok=True)
    indice, fichas = [], {}
    # regenerar solo algunos (navarra-demo tudela-bardenas) conserva los demás en el índice
    if (previo := destino / "index.json").exists():
        anteriores = json.loads(previo.read_text(encoding="utf-8"))
        indice = [e for e in anteriores if e["id"] not in ejemplos]
        fichas = json.loads((destino / "recursos.json").read_text(encoding="utf-8"))
    for id, peticion in ejemplos.items():
        s = grafo.invoke({"peticion": peticion})
        if s.get("pregunta"):
            raise SystemExit(f"{id}: el agente pregunta «{s['pregunta']}»; cambia la petición")
        doc = {
            "id": id,
            "origen": None,
            "peticion": peticion,
            "creado": datetime.now(UTC).isoformat(),
            "plan": api._publico(s["plan"]),
            "texto": s["texto"],
        }
        _json(destino / f"{id}.json", doc)
        (destino / f"{id}.gpx").write_bytes(api.a_gpx(doc))
        fichas |= {i: r for i in planner.ids(doc["plan"]) if (r := consultas.recurso(con, i))}
        indice.append({"id": id, "peticion": peticion})
        n = sum(len(d["paradas"]) for d in doc["plan"]["dias"])
        print(f"{id:22} {n} paradas" if n else f"{id:22} {len(doc['plan']['opciones'])} opciones")
    _json(destino / "recursos.json", fichas)
    orden = list(EJEMPLOS)  # el de EJEMPLOS, aunque se regeneren sueltos
    indice.sort(key=lambda e: orden.index(e["id"]) if e["id"] in orden else len(orden))
    _json(destino / "index.json", indice)
    return indice


def _json(ruta: Path, datos) -> None:
    ruta.write_text(json.dumps(datos, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> None:
    """`navarra-demo [id ...]`: todos los ejemplos o solo los indicados (los demás se conservan)."""
    solo = sys.argv[1:]
    ejemplos = {k: v for k, v in EJEMPLOS.items() if not solo or k in solo}
    con = consultas.conectar()
    exportar(agente.construir(agente.crear_llm(), con), con, ejemplos)
