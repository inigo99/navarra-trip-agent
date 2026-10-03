"""API HTTP para la web (`navarra-api`, documentación en http://localhost:8000/docs).

POST /plan devuelve el progreso del agente por SSE (un evento por paso del grafo) y, al final,
el plan guardado en data/planes/{id}.json para el enlace de compartir. GET /plan/{id}/gpx lo
exporta para el móvil o el GPS.
"""

import json
import os
import secrets
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi import Path as Ruta
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from navarra_trip import agente
from navarra_trip.tools import consultas

PLANES = Path("data/planes")
IdPlan = Annotated[str, Ruta(pattern=r"^[A-Za-z0-9_-]{8}$")]  # token_urlsafe(6): sin "../"

app = FastAPI(title="navarra-trip-agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("NAVARRA_CORS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class Peticion(BaseModel):
    peticion: str = Field(min_length=3, max_length=500)


class Ajuste(BaseModel):
    cambio: str = Field("", max_length=300)  # "el día 2 más tranquilo", "añade bodegas"
    quitar: list[str] = Field(default_factory=list, max_length=50)  # ids de lugares


@cache
def _db():
    return consultas.conectar()


@cache
def _llm():
    return agente.crear_llm()


def _con():
    return consultas.preparar(_db().cursor())  # un cursor por petición: DuckDB no es thread-safe


def _grafo():
    return agente.construir(_llm(), _con())


def _sse(datos: dict) -> str:
    return f"data: {json.dumps(datos, ensure_ascii=False, default=str)}\n\n"


def _publico(plan: dict) -> dict:
    return {k: v for k, v in plan.items() if k != "candidatos"}  # candidatos: solo para evaluar


def _guardar(peticion: str, estado: dict, origen: str | None = None) -> dict:
    PLANES.mkdir(parents=True, exist_ok=True)
    doc = {
        "id": secrets.token_urlsafe(6),
        "origen": origen,  # el plan ajustado es otro: el enlace del original no cambia
        "peticion": peticion,
        "creado": datetime.now(UTC).isoformat(),
        "plan": _publico(estado["plan"]),
        "texto": estado["texto"],
    }
    (PLANES / f"{doc['id']}.json").write_text(
        json.dumps(doc, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return doc


def _leer(id: str) -> dict:
    ruta = PLANES / f"{id}.json"
    if not ruta.is_file():
        raise HTTPException(404, "Plan no encontrado")
    return json.loads(ruta.read_text(encoding="utf-8"))


@app.get("/salud")
def salud() -> dict:
    return {"ok": True}


def _progreso(inicial: dict, origen: str | None = None) -> StreamingResponse:
    """Eventos SSE: {"paso": nodo} mientras trabaja; al final {"pregunta": ...} si falta algo
    o el plan guardado {"id", "peticion", "plan", "texto"}; {"error": ...} si algo falla
    (OSRM u Ollama caídos)."""
    grafo = _grafo()

    def eventos():
        estado: dict = {}
        try:
            for paso in grafo.stream(inicial, stream_mode="updates"):
                for nodo, cambios in paso.items():
                    estado |= cambios or {}
                    yield _sse({"paso": nodo})
            if estado.get("pregunta"):
                yield _sse({"pregunta": estado["pregunta"]})
            else:
                yield _sse(_guardar(inicial["peticion"], estado, origen))
        except Exception as e:  # al navegador como evento, no un 500 a medio stream
            yield _sse({"error": f"{type(e).__name__}: {e}"})

    # no-transform: sin él, el proxy de Next comprime con gzip y retiene los eventos hasta el final
    return StreamingResponse(
        eventos(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform"},
    )


@app.post("/plan")
def plan(p: Peticion) -> StreamingResponse:
    return _progreso({"peticion": p.peticion})


@app.post("/plan/{id}/ajustar")
def ajustar(id: IdPlan, a: Ajuste) -> StreamingResponse:
    """Rehace el plan con el cambio añadido a la petición y sin los lugares quitados.
    ponytail: replanifica todos los días (otros días pueden cambiar); si molesta, rehacer
    solo los días tocados reutilizando el resto."""
    if not a.cambio.strip() and not a.quitar:
        raise HTTPException(422, "Nada que ajustar")
    doc = _leer(id)
    peticion = doc["peticion"]
    if cambio := a.cambio.strip():
        peticion = f"{peticion.rstrip('. ')}. {cambio}"
    quitados = {*a.quitar, *(doc["plan"].get("excluidos") or [])}  # se acumulan entre ajustes
    return _progreso({"peticion": peticion, "excluir": sorted(quitados)}, origen=id)


@app.get("/plan/{id}")
def plan_guardado(id: IdPlan) -> dict:
    return _leer(id)


@app.get("/plan/{id}/gpx")
def gpx(id: IdPlan) -> Response:
    return Response(
        a_gpx(_leer(id)),
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="navarra-{id}.gpx"'},
    )


@app.get("/recurso/{id}")
def recurso(id: str) -> dict:
    """Ficha completa de un lugar para el panel de detalle (descripción, imagen, horario…)."""
    r = consultas.recurso(_con(), id)
    if r is None:
        raise HTTPException(404, "Recurso no encontrado")
    return r


def a_gpx(doc: dict) -> bytes:
    """Un waypoint por parada, comida y cena (con el día y la hora) y un track por día con la
    ruta de OSRM."""
    gpx = ET.Element(
        "gpx",
        version="1.1",
        creator="navarra-trip-agent",
        xmlns="http://www.topografix.com/GPX/1/1",
    )
    meta = ET.SubElement(gpx, "metadata")
    ET.SubElement(meta, "name").text = doc["peticion"]

    def wpt(lugar: dict, nombre: str, desc: str) -> None:
        w = ET.SubElement(gpx, "wpt", lat=str(lugar["lat"]), lon=str(lugar["lon"]))
        ET.SubElement(w, "name").text = nombre
        ET.SubElement(w, "desc").text = desc

    plan = doc["plan"]
    for d in plan["dias"]:
        for p in d["paradas"]:
            wpt(p, p["nombre"], f"Día {d['dia']}, {p['llegada']}")
        for clave in ("comida", "cena"):
            if d.get(clave):
                wpt(d[clave], d[clave]["nombre"], f"Día {d['dia']}, {clave}")
    for d in plan["dias"]:
        if (geo := d.get("geometria")) and geo.get("type") == "LineString":
            trk = ET.SubElement(gpx, "trk")
            ET.SubElement(trk, "name").text = f"Día {d['dia']}"
            seg = ET.SubElement(trk, "trkseg")
            for lon, lat in geo["coordinates"]:
                ET.SubElement(seg, "trkpt", lat=str(lat), lon=str(lon))
    return ET.tostring(gpx, encoding="utf-8", xml_declaration=True)


def main() -> None:
    import uvicorn

    uvicorn.run("navarra_trip.api:app", host="127.0.0.1", port=8000)
