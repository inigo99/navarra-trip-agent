"""Agente LangGraph: el LLM interpreta la petición y redacta; planner.py hace el resto.

interpretar → (falta algo: pregunta) → candidatos → tiempo → agrupar → ordenar → extras
→ redactar → comprobar (ids inventados o paradas sin citar: redacta otra vez; a la 2.ª,
texto de plantilla).

LLM con NAVARRA_LLM (por defecto 'ollama:qwen2.5:7b'; demo: 'anthropic:claude-haiku-4-5').
"""

import json
import os
import re
import sys
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from navarra_trip import planner
from navarra_trip.planner import Requisitos
from navarra_trip.tools import consultas, rutas, tiempo

LLM = os.environ.get("NAVARRA_LLM", "ollama:qwen2.5:7b")
INTENTOS = 2
CITA = re.compile(r"\[([a-z]+:[\w.-]+)\]")

INTERPRETAR = """Extraes los requisitos de una petición de viaje por Navarra.
Hoy es {hoy}. Próximos días: {semana}.
Convierte fechas relativas ("el sábado") en la fecha de esa lista.
base: el pueblo o ciudad que nombra como punto de partida o alojamiento.
Si no dice cuántos días (dias=0) o no nombra ningún lugar (base=""), no lo supongas.
"Tranquilo" o "sin prisa" = ritmo relajado; "intenso" o "ver mucho" = intenso.
idioma: el de la petición (es, en o fr).

Ejemplo: "Fin de semana de 2 días desde Olite, castillos y vino"
-> dias=2, base="Olite", intereses=["castillos", "vino"], ritmo="normal".
"Algo tranquilo, 3 días en Pamplona a pie" -> dias=3, base="Pamplona", transporte="pie",
ritmo="relajado"."""
DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

REDACTAR = """Eres un guía de Navarra. Escribe el itinerario en {idioma} usando SOLO los datos
del JSON. Reglas:
- Cita cada lugar, restaurante y alojamiento así: **Nombre** [id], con el id exacto del JSON.
- Cita todas las paradas, en el orden dado, con su hora de llegada.
- Sigue la agenda en su orden (paradas y comida). Usa las horas tal cual (llegada, hora,
  fin): no calcules ninguna.
- No añadas lugares, horarios, precios ni teléfonos que no estén. horario null = "consultar
  horario". Si hay aviso de tiempo o avisos, menciónalos.
- Markdown: un apartado por día; breve (2-3 frases por parada)."""

FALTA = {
    "es": {"dias": "¿Cuántos días dura el viaje?", "base": "¿En qué pueblo o ciudad te alojas?"},
    "en": {"dias": "How many days is the trip?", "base": "Which town will you stay in?"},
    "fr": {"dias": "Combien de jours dure le voyage ?", "base": "Dans quelle ville logez-vous ?"},
}
NO_ENCONTRADO = {
    "es": "No encuentro «{}» en Navarra. ¿Cuál es el pueblo más cercano?",
    "en": "I can't find “{}” in Navarre. What is the nearest town?",
    "fr": "Je ne trouve pas « {} » en Navarre. Quelle est la ville la plus proche ?",
}


class Estado(TypedDict, total=False):
    peticion: str
    requisitos: Requisitos
    pregunta: str
    base: dict
    candidatos: list[dict]
    prevision: list[dict | None]
    grupos: list[list[dict]]
    dias: list[dict]
    plan: dict
    texto: str
    error: str
    intentos: int


def construir(
    llm,
    con,
    *,
    sim=None,
    matriz=rutas.matriz,
    ruta=rutas.ruta,
    prevision=tiempo.prevision,
    hoy: date | None = None,
):
    """Grafo compilado. Todo lo externo se inyecta: los tests usan dobles sin red ni modelo."""
    hoy = hoy or date.today()
    avisos: list[str] = []
    semana = ", ".join(_dia(hoy + timedelta(days=i)) for i in range(1, 8))

    def interpretar(s: Estado) -> Estado:
        avisos.clear()
        req = llm.with_structured_output(Requisitos).invoke(
            [("system", INTERPRETAR.format(hoy=_dia(hoy), semana=semana)), ("human", s["peticion"])]
        )
        if f := fecha_relativa(s["peticion"], hoy):
            req.fecha_inicio = f  # qwen2.5:7b fallaba el día de la semana aun con la lista
        falta = [FALTA[req.idioma][c] for c in ("dias", "base") if not getattr(req, c)]
        if falta:
            return {"requisitos": req, "pregunta": " ".join(falta)}
        base = planner.lugar(con, req.base)
        if base is None:
            return {"requisitos": req, "pregunta": NO_ENCONTRADO[req.idioma].format(req.base)}
        return {"requisitos": req, "base": base}

    def candidatos(s: Estado) -> Estado:
        similitud = sim or planner.similitud_por_defecto(con)
        return {"candidatos": planner.candidatos(con, s["requisitos"], s["base"], similitud)}

    def prevision_(s: Estado) -> Estado:
        try:
            dias, aviso = planner.prevision_viaje(s["base"], s["requisitos"], hoy, prevision)
        except Exception as e:  # sin previsión el plan sigue valiendo
            dias, aviso = [None] * s["requisitos"].dias, f"Sin previsión del tiempo ({e})."
        if aviso:
            avisos.append(aviso)
        return {"prevision": dias}

    def agrupar(s: Estado) -> Estado:
        req = s["requisitos"]
        grupos = planner.agrupar(s["base"], s["candidatos"], req, s["prevision"], matriz)
        for i, p in enumerate(s["prevision"]):
            if p and p["mal_tiempo"]:
                avisos.append(
                    f"Día {i + 1}: previsión de {p['descripcion']}; van primero monumentos."
                )
        return {"grupos": grupos}

    def ordenar(s: Estado) -> Estado:
        dias = [planner.ordenar(s["base"], g, s["requisitos"], matriz, ruta) for g in s["grupos"]]
        if any(not d["paradas"] for d in dias):
            avisos.append("Algún día queda sin paradas: no hay bastantes lugares cerca de la base.")
        return {"dias": dias}

    def extras(s: Estado) -> Estado:
        req, usados, dias = s["requisitos"], set(), []
        for i, (d, p) in enumerate(zip(s["dias"], s["prevision"], strict=True)):
            c = None
            if d["paradas"]:
                c = planner.comida(con, d["paradas"][d["comida_tras"]], req.transporte, usados)
            fecha = p["fecha"] if p else None
            if not fecha and req.fecha_inicio:
                fecha = (req.fecha_inicio + timedelta(days=i)).isoformat()
            dias.append(d | {"dia": i + 1, "fecha": fecha, "tiempo": p, "comida": c})
        plan = {
            "requisitos": req.model_dump(mode="json"),
            "base": s["base"],
            "alojamientos": planner.alojamientos(con, s["base"], req),
            "dias": dias,
            "avisos": list(avisos),
            # para depurar y evaluar (S4): qué se consideró y con qué puntuación
            "candidatos": [
                {"id": r["id"], "nombre": r["nombre"], "puntos": round(r["puntos"], 3)}
                for r in s["candidatos"]
            ],
        }
        return {"plan": plan | {"geojson": planner.geojson(plan)}, "intentos": 0}

    def redactar(s: Estado) -> Estado:
        plan = s["plan"]
        msgs = [
            ("system", REDACTAR.format(idioma=plan["requisitos"]["idioma"])),
            ("human", json.dumps(_para_llm(plan), ensure_ascii=False)),
        ]
        if s.get("error"):
            msgs += [("ai", s["texto"]), ("human", f"Corrige esto y reescríbelo: {s['error']}")]
        return {"texto": llm.invoke(msgs).content, "intentos": s["intentos"] + 1}

    def comprobar(s: Estado) -> Estado:
        validos = planner.ids(s["plan"])
        citados = set(CITA.findall(s["texto"]))
        inventados = citados - validos
        sin_citar = {p["id"] for d in s["plan"]["dias"] for p in d["paradas"]} - citados
        if not inventados and not sin_citar:
            return {"error": None}
        error = ""
        if inventados:
            error += f"Estos ids no existen en el JSON: {sorted(inventados)}. "
        if sin_citar:
            error += f"Faltan estas paradas: {sorted(sin_citar)}."
        if s["intentos"] >= INTENTOS:
            return {"texto": plantilla(s["plan"]), "error": None}
        return {"error": error}

    g = StateGraph(Estado)
    for nombre, f in [
        ("interpretar", interpretar),
        ("candidatos", candidatos),
        ("tiempo", prevision_),
        ("agrupar", agrupar),
        ("ordenar", ordenar),
        ("extras", extras),
        ("redactar", redactar),
        ("comprobar", comprobar),
    ]:
        g.add_node(nombre, f)
    g.add_edge(START, "interpretar")
    g.add_conditional_edges("interpretar", lambda s: END if s.get("pregunta") else "candidatos")
    for a, b in pairwise(
        ["candidatos", "tiempo", "agrupar", "ordenar", "extras", "redactar", "comprobar"]
    ):
        g.add_edge(a, b)
    g.add_conditional_edges("comprobar", lambda s: "redactar" if s.get("error") else END)
    return g.compile()


SEMANA = {  # es, en, fr -> weekday()
    **dict.fromkeys(("lunes", "monday", "lundi"), 0),
    **dict.fromkeys(("martes", "tuesday", "mardi"), 1),
    **dict.fromkeys(("miercoles", "wednesday", "mercredi"), 2),
    **dict.fromkeys(("jueves", "thursday", "jeudi"), 3),
    **dict.fromkeys(("viernes", "friday", "vendredi"), 4),
    **dict.fromkeys(("sabado", "saturday", "samedi", "fin de semana", "weekend"), 5),
    **dict.fromkeys(("domingo", "sunday", "dimanche"), 6),
}
MANANA = ("pasado manana", "manana", "tomorrow", "demain")


def fecha_relativa(texto: str, hoy: date) -> date | None:
    """ "el sábado", "mañana", "this weekend"... -> fecha. Las fechas explícitas, del LLM."""
    t = consultas._sin_tildes(texto).replace("week-end", "weekend")
    for palabra, wd in SEMANA.items():
        if re.search(rf"\b{palabra}\b", t):
            return hoy + timedelta(days=(wd - hoy.weekday()) % 7)
    for i, palabra in enumerate(MANANA):
        if re.search(rf"\b{palabra}\b", t):
            return hoy + timedelta(days=2 if i == 0 else 1)
    return None


def _mas(hora: str, minutos: int) -> str:
    h, m = map(int, hora.split(":"))
    return planner._hora(h * 60 + m + minutos)


def _dia(d: date) -> str:
    return f"{DIAS_SEMANA[d.weekday()]} {d.isoformat()}"


def _para_llm(plan: dict) -> dict:
    """Solo lo que necesita para redactar: sin coordenadas, geometrías ni puntuaciones."""

    def p(r):
        claves = (
            "id",
            "nombre",
            "municipio",
            "categoria",
            "llegada",
            "duracion_min",
            "trayecto_min",
            "descripcion",
            "horario",
            "visitantes_12m",
        )
        out = {k: r.get(k) for k in claves}
        out["fin"] = _mas(r["llegada"], r["duracion_min"])
        return out | {"tipo": "parada"}

    def agenda(d):
        """Paradas y comida en orden: con la comida aparte, el modelo la ponía al final."""
        out = [p(r) for r in d["paradas"]]
        if d["comida"]:
            c = d["comida"]
            hora = d["paradas"][d["comida_tras"]]["comida_despues"]
            comida = {k: c[k] for k in ("id", "nombre", "especialidad")}
            fin = _mas(hora, planner.COMIDA)
            out.insert(d["comida_tras"] + 1, {"tipo": "comida", "hora": hora, "fin": fin} | comida)
        return out

    return {
        "base": plan["base"]["nombre"],
        "alojamientos": [
            {k: a[k] for k in ("id", "nombre", "modalidad", "localidad")}
            for a in plan["alojamientos"]
        ],
        "dias": [
            {
                "dia": d["dia"],
                "fecha": d["fecha"],
                "tiempo": d["tiempo"]
                and {k: d["tiempo"][k] for k in ("descripcion", "max", "min", "mal_tiempo")},
                "agenda": agenda(d),
                "vuelta_a_la_base": d.get("vuelta"),
                "km": d["km"],
            }
            for d in plan["dias"]
        ],
        "avisos": plan["avisos"],
    }


def plantilla(plan: dict) -> str:
    """Texto sin LLM, por si el modelo no consigue citar bien."""
    out = []
    for d in plan["dias"]:
        out.append(f"## Día {d['dia']}" + (f" ({d['fecha']})" if d["fecha"] else ""))
        for r in d["paradas"]:
            out.append(f"- {r['llegada']} **{r['nombre']}** [{r['id']}], {r['municipio']}")
        if d["comida"]:
            out.append(f"- Comida: **{d['comida']['nombre']}** [{d['comida']['id']}]")
    out += [f"- Alojamiento: **{a['nombre']}** [{a['id']}]" for a in plan["alojamientos"]]
    return "\n".join(out + [f"> {a}" for a in plan["avisos"]])


def main() -> None:
    """`navarra-plan "3 días en Estella, románico y naturaleza"`: texto por pantalla y el plan
    en data/plan.json (con el GeoJSON para el mapa)."""
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(LLM, temperature=0)
    grafo = construir(llm, consultas.conectar())
    s = grafo.invoke({"peticion": " ".join(sys.argv[1:])})
    if s.get("pregunta"):
        print(s["pregunta"], f"\n(entendido: {s['requisitos'].model_dump_json()})")
        return
    Path("data/plan.json").write_text(
        json.dumps(s["plan"], ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(s["texto"] + "\n\n(plan completo en data/plan.json)")
