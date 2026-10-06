"""Agente LangGraph: el LLM interpreta la petición y redacta; planner.py hace el resto.

interpretar → (falta algo: pregunta) → candidatos → tiempo → agrupar → ordenar → extras
→ redactar → comprobar (paradas sin frase: las pide otra vez; a la 2.ª, sin frase).
Peticiones de opciones ("bares de pintxos en…"): interpretar → buscar → redactar → comprobar.

El texto lo compone el código (días, horas, comidas, vuelta, alojamientos) y el LLM solo
escribe una o dos frases por parada: escribiendo el itinerario entero, qwen2.5:7b ponía la
vuelta después de la cena, "tiempo libre 21:00" o se saltaba lugares.

LLM con NAVARRA_LLM: por defecto 'ollama:qwen2.5:7b' en local; en el servidor de la demo,
'groq:openai/gpt-oss-120b' (API gratuita, GROQ_API_KEY). Cualquier proveedor de LangChain.
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
from pydantic import BaseModel, Field

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
idioma: el de la petición (es, en, fr o eu = euskera).

Ejemplo: "Fin de semana de 2 días desde Olite, castillos y vino"
-> dias=2, base="Olite", intereses=["castillos", "vino"], ritmo="normal".
"Algo tranquilo, 3 días en Pamplona a pie" -> dias=3, base="Pamplona", transporte="pie",
ritmo="relajado".
"Quiero hacer una ruta de monte cerca de Isaba" -> tipo="opciones", base="Isaba",
intereses=["ruta de monte"].
"Dime bares de pintxos en Estella" -> tipo="opciones", base="Estella", intereses=["pintxos"]."""
IDIOMAS = {"es": "español", "en": "inglés", "fr": "francés", "eu": "euskera"}
# la orden también en el idioma pedido: los modelos pequeños copiaban el de las descripciones
ESCRIBE = {
    "es": "",
    "en": "\nWrite every sentence in English.",
    "fr": "\nÉcris chaque phrase en français.",
    "eu": "\nIdatzi esaldi guztiak euskaraz.",
}
# "dime…", "recomiéndame…", "bares de…": pide opciones aunque el LLM diga plan
PIDE_OPCIONES = re.compile(
    r"\b(dime|recomiend\w*|sugier\w*|opciones|ideas|una ruta|rutas|bares|restaurantes?|donde "
    r"(comer|cenar|tomar)|tell me|suggest\w*|recommend\w*|options|where to|bars|conseill\w*|"
    r"idees|ou manger)\b"
)
FRASE_MAX = 400  # 1-2 frases; más largo, bucle
# sin cuántos días ni fechas, no se suponen (se pregunta); "paso el día", "finde", "egun"...
DURACION = re.compile(
    r"\d|\b(dias?|days?|jours?|journees?|semanas?|weeks?|semaines?|finde|fin de semana|weekend"
    r"|week-end|noches?|nights?|nuits?|egun\w*|asteburu\w*)\b"
)
DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

REDACTAR = """Eres un guía de Navarra. Para cada lugar del JSON escribe en {idioma} una o dos
frases que inviten a visitarlo, usando SOLO su descripción, horario y precio. No repitas el
nombre ni pongas horas de llegada (ya van en el itinerario); horario null = no hables de
horario. No añadas datos que no estén. Traduce también los avisos al {idioma}, en su orden."""


class Frase(BaseModel):
    id: str = Field(description="id exacto del lugar en el JSON")
    texto: str = Field(description="1-2 frases")


class Redaccion(BaseModel):
    lugares: list[Frase] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list, description="los avisos, traducidos")


FALTA = {
    "es": {"dias": "¿Cuántos días dura el viaje?", "base": "¿En qué pueblo o ciudad te alojas?"},
    "en": {"dias": "How many days is the trip?", "base": "Which town will you stay in?"},
    "fr": {"dias": "Combien de jours dure le voyage ?", "base": "Dans quelle ville logez-vous ?"},
    "eu": {"dias": "Zenbat egunerako da bidaia?", "base": "Zein herritan ostatuko zara?"},
}
NO_ENCONTRADO = {
    "es": "No encuentro «{}» en Navarra. ¿Cuál es el pueblo más cercano?",
    "en": "I can't find “{}” in Navarre. What is the nearest town?",
    "fr": "Je ne trouve pas « {} » en Navarre. Quelle est la ville la plus proche ?",
    "eu": "Ez dut «{}» aurkitzen Nafarroan. Zein da herririk hurbilena?",
}


class Estado(TypedDict, total=False):
    peticion: str
    excluir: list[str]  # ids quitados al ajustar un plan
    requisitos: Requisitos
    pregunta: str
    base: dict
    candidatos: list[dict]
    prevision: list[dict | None]
    grupos: list[list[dict]]
    dias: list[dict]
    plan: dict
    texto: str
    frases: dict[str, str]
    avisos_txt: list[str]
    faltan: list[str]
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
        if i := idioma_de(s["peticion"]):
            req.idioma = i  # qwen2.5:7b ponía "es" a todas las peticiones en inglés y francés
        if req.idioma == "eu" and (d := dias_euskera(s["peticion"])):
            req.dias = d
        if f := fecha_relativa(s["peticion"], hoy):
            req.fecha_inicio = f  # qwen2.5:7b fallaba el día de la semana aun con la lista
        _respaldar(req, s["peticion"])
        texto = consultas._sin_tildes(s["peticion"])
        if not DURACION.search(texto):
            req.dias = 0  # qwen2.5:3b ponía días a "Quiero ver Navarra desde Pamplona"
            if PIDE_OPCIONES.search(texto):
                req.tipo = "opciones"
        elif req.dias:
            req.tipo = "plan"  # con días es un viaje, aunque el LLM diga opciones
        if req.dias > planner.MAX_DIAS:
            avisos.append(f"El plan se limita a {planner.MAX_DIAS} días (pediste {req.dias}).")
            req.dias = planner.MAX_DIAS
        necesita = ("base",) if req.tipo == "opciones" else ("dias", "base")
        falta = [FALTA[req.idioma][c] for c in necesita if not getattr(req, c)]
        if falta:
            return {"requisitos": req, "pregunta": " ".join(falta)}
        base = planner.lugar(con, req.base)
        if base is None:
            return {"requisitos": req, "pregunta": NO_ENCONTRADO[req.idioma].format(req.base)}
        return {"requisitos": req, "base": base}

    def buscar(s: Estado) -> Estado:
        """Petición de opciones: lugares, rutas, bares o restaurantes, sin itinerario."""
        req, base = s["requisitos"], s["base"]
        similitud = sim or planner.similitud_por_defecto(con)
        fuera = frozenset(s.get("excluir") or ())
        ops = planner.opciones(con, req, base, similitud, s["peticion"], fuera)
        if not ops:
            avisos.append(f"No encuentro nada así cerca de {base['nombre']}.")
        if any(o.get("categoria") == "bodega" for o in ops):
            avisos.append("Las bodegas suelen pedir reserva para visitarlas: confírmalo en su web.")
        plan = {
            "requisitos": req.model_dump(mode="json"),
            "base": base,
            "alojamientos": [],
            "dias": [],
            "opciones": ops,
            "avisos": list(avisos),
            "excluidos": s.get("excluir") or [],
            "candidatos": [],
        }
        return {"plan": plan | {"geojson": planner.geojson(plan)}, "intentos": 0}

    def candidatos(s: Estado) -> Estado:
        similitud = sim or planner.similitud_por_defecto(con)
        req, base = s["requisitos"], s["base"]
        for i in planner.intereses_lejanos(con, req, base, similitud):
            avisos.append(
                f"Cerca de {base['nombre']} ({planner.RADIO_KM[req.transporte]} km, "
                f"{req.transporte}) no hay nada destacado de «{i}»: lo más parecido queda lejos."
            )
        fuera = frozenset(s.get("excluir") or ())
        return {"candidatos": planner.candidatos(con, req, base, similitud, fuera)}

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
        req, usados, dias = s["requisitos"], set(s.get("excluir") or ()), []
        pintxos, sin_ronda = planner.quiere_pintxos(req), False
        pedidos = planner.restaurantes_pedidos(con, s["base"], s["peticion"])
        for i, (d, p) in enumerate(zip(s["dias"], s["prevision"], strict=True)):
            c = None
            if d["paradas"] and not (antes := d["paradas"][d["comida_tras"]]).get("picnic"):
                c = planner.comida(con, antes, req.transporte, usados, pedidos)
                if (
                    c and c["id"] in pedidos
                ):  # el pedido, una vez: salía Burger King a comer y cenar
                    pedidos = set()
            fecha = p["fecha"] if p else None
            if not fecha and req.fecha_inicio:
                fecha = (req.fecha_inicio + timedelta(days=i)).isoformat()
            noche = {"ronda": planner.ronda(con, s["base"], usados)} if pintxos else {}
            if not noche.get("ronda"):
                noche = {"cena": planner.cena(con, s["base"], usados, pedidos)}
                if noche["cena"] and noche["cena"]["id"] in pedidos:
                    pedidos = set()
                if pintxos and not sin_ronda:
                    sin_ronda = True
                    avisos.append(
                        f"No hay bastantes bares juntos cerca de {s['base']['nombre']} para una "
                        "ronda de pintxos: se propone cenar en un restaurante."
                    )
            dias.append(d | {"dia": i + 1, "fecha": fecha, "tiempo": p, "comida": c} | noche)
        if any(p["categoria"] == "bodega" for d in dias for p in d["paradas"]):
            avisos.append("Las bodegas suelen pedir reserva para visitarlas: confírmalo en su web.")
        plan = {
            "requisitos": req.model_dump(mode="json"),
            "base": s["base"],
            # en un día no se duerme fuera: sin alojamientos
            "alojamientos": planner.alojamientos(con, s["base"], req) if req.dias > 1 else [],
            "dias": dias,
            "avisos": list(avisos),
            "excluidos": s.get("excluir") or [],
            # para depurar y evaluar (S4): qué se consideró y con qué puntuación
            "candidatos": [
                {"id": r["id"], "nombre": r["nombre"], "puntos": round(r["puntos"], 3)}
                for r in s["candidatos"]
            ],
        }
        return {"plan": plan | {"geojson": planner.geojson(plan)}, "intentos": 0}

    def redactar(s: Estado) -> Estado:
        """Una llamada por día: con los 4 días de Pamplona de una vez (36 lugares) el JSON
        pasaba del tope de tokens, llegaba cortado y el plan salía sin ninguna frase."""
        plan, frases = s["plan"], dict(s.get("frases") or {})
        codigo = plan["requisitos"]["idioma"]
        idioma = IDIOMAS[codigo]
        faltan = set(s.get("faltan") or [p["id"] for g in _grupos(plan) for p in g])
        avisos = s.get("avisos_txt")
        traducir = plan["avisos"] if plan["requisitos"]["idioma"] != "es" and not avisos else []
        validas = {p["id"]: p for g in _grupos(plan) for p in g}
        for grupo in _grupos(plan) or [[]]:  # [[]]: sin lugares, aún hay avisos que traducir
            pedir = {p["id"] for p in grupo} & faltan
            if not pedir and not traducir:
                continue
            datos = {"lugares": _lugares(plan, pedir), "avisos": traducir}
            msgs = [
                ("system", REDACTAR.format(idioma=idioma) + ESCRIBE[codigo]),
                ("human", json.dumps(datos, ensure_ascii=False)),
            ]
            try:
                r = llm.with_structured_output(Redaccion).invoke(msgs)
            except Exception:  # JSON roto (p. ej. cortado por el tope de tokens): sin frases
                continue
            for f in r.lugares:
                if (
                    f.id in pedir
                    and _sin_horas_inventadas(f.texto, validas[f.id])
                    and _frase_valida(f.texto, codigo)
                ):
                    frases[f.id] = re.sub(r"\s*\[[^\]]*\]", "", f.texto).strip()  # sin citas
            if traducir and len(r.avisos) == len(traducir):
                avisos, traducir = r.avisos, []
        return {"frases": frases, "avisos_txt": avisos, "intentos": s["intentos"] + 1}

    def comprobar(s: Estado) -> Estado:
        plan = s["plan"]
        faltan = [p["id"] for g in _grupos(plan) for p in g if p["id"] not in s["frases"]]
        if faltan and s["intentos"] < INTENTOS:
            return {"faltan": faltan}
        avisos = s.get("avisos_txt") or plan["avisos"]
        texto = componer(plan, s["frases"], avisos)
        # para la web: frases y avisos traducidos van con el plan (pinta el itinerario ella)
        return {
            "faltan": [],
            "texto": texto,
            "plan": plan | {"frases": s["frases"], "avisos_txt": avisos},
        }

    g = StateGraph(Estado)
    for nombre, f in [
        ("interpretar", interpretar),
        ("buscar", buscar),
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
    g.add_conditional_edges(
        "interpretar",
        lambda s: (
            END
            if s.get("pregunta")
            else "buscar"
            if s["requisitos"].tipo == "opciones"
            else "candidatos"
        ),
    )
    g.add_edge("buscar", "redactar")
    for a, b in pairwise(
        ["candidatos", "tiempo", "agrupar", "ordenar", "extras", "redactar", "comprobar"]
    ):
        g.add_edge(a, b)
    g.add_conditional_edges("comprobar", lambda s: "redactar" if s.get("faltan") else END)
    return g.compile()


SEMANA = {  # es, en, fr, eu -> weekday(); por prefijo: 'larunbatean', 'asteburuan'
    **dict.fromkeys(("lunes", "monday", "lundi", "astelehen"), 0),
    **dict.fromkeys(("martes", "tuesday", "mardi", "astearte"), 1),
    **dict.fromkeys(("miercoles", "wednesday", "mercredi", "asteazken"), 2),
    **dict.fromkeys(("jueves", "thursday", "jeudi", "ostegun"), 3),
    **dict.fromkeys(("viernes", "friday", "vendredi", "ostiral"), 4),
    **dict.fromkeys(
        (
            "sabado",
            "saturday",
            "samedi",
            "fin de semana",
            "finde",
            "weekend",
            "larunbat",
            "asteburu",
        ),
        5,
    ),
    **dict.fromkeys(("domingo", "sunday", "dimanche", "igande"), 6),
}
EGUNAK = {"bat": 1, "bi": 2, "hiru": 3, "lau": 4, "bost": 5, "sei": 6, "zazpi": 7}


def dias_euskera(texto: str) -> int | None:
    """'Hiru egun', 'Egun bat', 'asteburua': qwen2.5:7b fallaba los días en euskera."""
    t = consultas._sin_tildes(texto)
    if m := re.search(r"\b(bi|hiru|lau|bost|sei|zazpi) egun|\begun (bat)\b", t):
        return EGUNAK[m[1] or m[2]]
    return 2 if re.search(r"\basteburu", t) else None


MANANA = ("pasado manana", "manana", "tomorrow", "demain")
NUMEROS = {
    **dict.fromkeys(("un", "una", "uno", "a", "one", "une"), 1),
    **dict.fromkeys(("dos", "two", "deux"), 2),
    **dict.fromkeys(("tres", "three", "trois"), 3),
    **dict.fromkeys(("cuatro", "four", "quatre"), 4),
    **dict.fromkeys(("cinco", "five", "cinq"), 5),
    **dict.fromkeys(("seis", "six"), 6),
}
UNIDADES = {
    **dict.fromkeys(("dia", "dias", "jour", "jours"), 1),
    **dict.fromkeys(("semana", "semanas", "week", "weeks", "semaine", "semaines"), 7),
    **dict.fromkeys(("mes", "meses", "month", "months", "mois"), 30),
}
# "in 3 days" no entra: "Pamplona in 3 days" es la duración
DENTRO = re.compile(
    r"\b(?:dentro de|dans|in) (\d+|[a-z]+) (dias?|semanas?|mes(?:es)?|jours?|semaines?|mois"
    r"|weeks?|months?)\b"
)


def fecha_relativa(texto: str, hoy: date) -> date | None:
    """ "el sábado", "mañana", "this weekend"... -> fecha. Las fechas explícitas, del LLM."""
    t = consultas._sin_tildes(texto).replace("week-end", "weekend")
    m = DENTRO.search(t)
    n = m and (NUMEROS.get(m[1]) or (m[1].isdigit() and int(m[1])))
    if n and not (m[0].startswith("in ") and UNIDADES[m[2]] == 1):
        return hoy + timedelta(days=n * UNIDADES[m[2]])
    for palabra, wd in SEMANA.items():
        if re.search(rf"\b{palabra}", t):
            return hoy + timedelta(days=(wd - hoy.weekday()) % 7)
    for i, palabra in enumerate(MANANA):
        if re.search(rf"\b{palabra}\b", t):
            return hoy + timedelta(days=2 if i == 0 else 1)
    return None


PALABRAS = {  # frecuentes y de viaje: las peticiones son cortas
    "es": {
        "el",
        "y",
        "los",
        "las",
        "del",
        "con",
        "para",
        "una",
        "por",
        "se",
        "que",
        "dia",
        "dias",
        "vuelta",
        "llegada",
    },
    "en": {
        "the",
        "and",
        "of",
        "to",
        "with",
        "for",
        "is",
        "you",
        "your",
        "in",
        "day",
        "days",
        "lunch",
        "return",
        "arrival",
    },
    "fr": {
        "le",
        "les",
        "et",
        "des",
        "du",
        "avec",
        "pour",
        "une",
        "est",
        "vous",
        "au",
        "jours",
        "journee",
    },
    "eu": {
        "eta",
        "da",
        "bat",
        "dago",
        "ere",
        "egun",
        "baino",
        "dira",
        "zure",
        "hau",
        "eguna",
        "bazkaria",
        "itzulera",
    },
}


def idioma_de(texto: str) -> str | None:
    """Detector mínimo por palabras frecuentes: basta para distinguir es/en/fr/eu."""
    # fuera los nombres de lugar (**Iglesia de…** [mon:1]): en es aunque el texto sea fr
    nombre = r"\*\*[^*\n]*\[[^\]]*\][^*\n]*\*\*|\*\*[^*\n]+\*\*\s*\[[^\]]*\]|\[[^\]]*\]"
    texto = re.sub(nombre, " ", texto or "")
    palabras = consultas._sin_tildes(texto).split()
    palabras = [w for p in palabras for w in re.findall(r"[a-z]+", p)]
    cuenta = {i: sum(p in ps for p in palabras) for i, ps in PALABRAS.items()}
    mejor = max(cuenta, key=cuenta.get)
    return mejor if cuenta[mejor] else None


PIE = ("a pie", "andando", "caminando", "paseando", "walking", "on foot", "a pied", "oinez")
RITMOS = {
    "relajado": ("tranquil", "relaj", "sin prisa", "relax", "calma", "lasai"),
    "intenso": ("intens", "busy", "a tope", "sin parar", "packed"),
}


def _respaldar(req: Requisitos, peticion: str) -> None:
    """Transporte y ritmo, de lo que dice la petición: en euskera qwen2.5:7b se inventaba 'pie'
    y 'relajado' (y con 4 km desde Isaba el plan se quedaba en una parada)."""
    t = consultas._sin_tildes(peticion)
    t_pie = re.sub(r"\b(rutas?|senderos?|paseos?) a pie", "", t)  # n13: "rutas a pie por el río"
    # y al revés: en "Un día a pie por Estella" decía coche y mandaba a Los Arcos
    req.transporte = "pie" if any(p in t_pie for p in PIE) else "coche"
    if req.ritmo != "normal" and not any(p in t for p in RITMOS[req.ritmo]):
        req.ritmo = "normal"


PICNIC = "picnic durante la ruta: llevar comida y agua"  # al LLM; él lo traduce
LIBRE_DESDE = "18:00"  # si el día acaba antes, la tarde se dice libre: no se rellena con nada


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
            "precio",
            "web",
            "visitantes_12m",
            "longitud_km",
            "desnivel_m",
            "cimas",
        )
        out = {k: r.get(k) for k in claves}
        out["fin"] = _mas(r["llegada"], r["duracion_min"])
        return out | {"tipo": "parada"}

    def agenda(d):
        """Paradas y comida en orden: con la comida aparte, el modelo la ponía al final."""
        out = []
        for r in d["paradas"]:
            out.append(p(r))
            if r.get("picnic"):  # un dato en su sitio: como regla del prompt, la aplicaba a todo
                out.append({"tipo": "comida", "hora": "durante la ruta", "nota": PICNIC})
        if d["comida"]:
            c = d["comida"]
            hora = d["paradas"][d["comida_tras"]]["comida_despues"]
            comida = {k: c[k] for k in ("id", "nombre", "especialidad")}
            fin = _mas(hora, planner.COMIDA)
            out.insert(d["comida_tras"] + 1, {"tipo": "comida", "hora": hora, "fin": fin} | comida)
        if d["paradas"]:
            # como paso de la agenda, antes de la cena: suelta, qwen la ponía después ("cena a
            # las 21:00 ... vuelta a la base a las 15:05"). Sin salir del pueblo, no hay vuelta
            tipo = (
                "fin del recorrido" if d.get("en_la_base") else f"vuelta a {plan['base']['nombre']}"
            )
            out.append({"tipo": tipo, "hora": d["vuelta"]})
            if d["vuelta"] < LIBRE_DESDE:
                hasta = planner.HORA_RONDA if d.get("ronda") else planner.HORA_CENA
                libre = {"tipo": "tiempo libre", "desde": d["vuelta"], "hasta": hasta}
                out.append(libre | {"en": plan["base"]["nombre"]})
        if d.get("cena"):
            out.append(
                {"tipo": "cena"}
                | {k: d["cena"][k] for k in ("id", "nombre", "hora", "fin", "especialidad")}
            )
        if d.get("ronda"):
            bares = [{k: b[k] for k in ("id", "nombre", "horario")} for b in d["ronda"]]
            # sin el pueblo, qwen la situaba en la última parada ("ronda en Olcoz")
            donde = plan["base"]["nombre"]
            out.append(
                {
                    "tipo": "ronda de pintxos",
                    "hora": planner.HORA_RONDA,
                    "en": donde,
                    "bares": bares,
                }
            )
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
                "km": d["km"],
            }
            for d in plan["dias"]
        ],
        "avisos": plan["avisos"],
    }


ETIQUETAS = {  # el texto, en el idioma pedido
    "es": {
        "dia": "Día {}",
        "comida": "Comida",
        "cena": "Cena",
        "pintxos": "Ronda de pintxos",
        "alojamientos": "Dónde dormir",
        "picnic": "Comida de picnic durante la ruta: lleva comida y agua",
        "fin": "Fin del recorrido",
        "vuelta": "Vuelta a {}",
        "libre": "Tiempo libre en {}",
        "nota": "Horarios y precios pueden cambiar; confírmalos en la web de cada lugar.",
        "opciones": "Opciones cerca de {}",
        "duracion": "duración",
        "a": "a {}",
    },
    "en": {
        "dia": "Day {}",
        "comida": "Lunch",
        "cena": "Dinner",
        "pintxos": "Pintxos crawl",
        "alojamientos": "Where to stay",
        "picnic": "Picnic lunch on the trail: bring food and water",
        "fin": "End of the day's tour",
        "vuelta": "Back to {}",
        "libre": "Free time in {}",
        "nota": "Opening hours and prices may change; check each place's website.",
        "opciones": "Options near {}",
        "duracion": "duration",
        "a": "{} away",
    },
    "fr": {
        "dia": "Jour {}",
        "comida": "Déjeuner",
        "cena": "Dîner",
        "pintxos": "Tournée de pintxos",
        "alojamientos": "Où dormir",
        "picnic": "Pique-nique pendant la randonnée : prévoyez eau et nourriture",
        "fin": "Fin du parcours",
        "vuelta": "Retour à {}",
        "libre": "Temps libre à {}",
        "nota": "Horaires et prix peuvent changer ; vérifiez-les sur le site de chaque lieu.",
        "opciones": "Options près de {}",
        "duracion": "durée",
        "a": "à {}",
    },
    "eu": {
        "dia": "{}. eguna",
        "comida": "Bazkaria",
        "cena": "Afaria",
        "pintxos": "Pintxo-poteoa",
        "alojamientos": "Non lo egin",
        "picnic": "Piknika ibilbidean: eraman janaria eta ura",
        "fin": "Ibilbidearen amaiera",
        "vuelta": "Itzulera: {}",
        "libre": "Denbora librea: {}",
        "nota": "Ordutegiak eta prezioak alda daitezke; egiaztatu leku bakoitzaren webgunean.",
        "opciones": "Aukerak {} inguruan",
        "duracion": "iraupena",
        "a": "{}ra",
    },
}
HORA = re.compile(r"\b\d{1,2}[:.h]\d{2}\b")


def _grupos(plan: dict) -> list[list[dict]]:
    """Lugares a los que el LLM escribe frases, una llamada por grupo: las paradas de cada día
    o las opciones con descripción (bares y restaurantes no traen: inventaría)."""
    if "opciones" in plan:
        con_desc = [o for o in plan["opciones"] if o.get("descripcion")]
        return [con_desc] if con_desc else []
    return [d["paradas"] for d in plan["dias"]]


def _lugares(plan: dict, ids: set[str]) -> list[dict]:
    """Lo que el LLM necesita para describir cada parada, sin horas del plan."""
    claves = ("id", "nombre", "municipio", "descripcion", "horario", "precio", "cimas")
    return [{k: p.get(k) for k in claves} for g in _grupos(plan) for p in g if p["id"] in ids]


def _frase_valida(frase: str, idioma: str) -> bool:
    """Fuera las frases en otro idioma (qwen2.5:3b escribía en español 1 de cada 10 frases de
    planes en inglés) y las que entran en bucle ("Liédenan Liédenan…")."""
    detectado = idioma_de(frase)
    return len(frase) <= FRASE_MAX and detectado in (idioma, None)


def _sin_horas_inventadas(frase: str, p: dict) -> bool:
    """Una hora en la frase tiene que estar en el horario del lugar (si no, es inventada)."""
    return all(
        h.replace(".", ":").replace("h", ":") in (p.get("horario") or "")
        for h in HORA.findall(frase)
    )


def _detalle(p: dict) -> str:
    """Datos de ruta del plan, por código: no dependen del LLM."""
    partes = []
    if p.get("longitud_km"):
        partes.append(f"{p['longitud_km']} km")
    if p.get("desnivel_m"):
        partes.append(f"+{p['desnivel_m']} m")
    if p.get("cimas"):
        partes.append(f"⛰ {p['cimas']}")
    return f" ({', '.join(partes)})" if partes else ""


def _duracion(minutos: int) -> str:
    h, m = divmod(minutos, 60)
    return f"{h} h {m:02d} min" if h and m else f"{h} h" if h else f"{m} min"


def _opciones(plan: dict, frases: dict[str, str], et: dict) -> list[str]:
    """Lista sin horarios: lugar, duración de la visita o la ruta, distancia y frase."""
    out = ["## " + et["opciones"].format(plan["base"]["nombre"])]
    for o in plan["opciones"]:
        datos = [o.get("municipio") or o.get("localidad"), o.get("especialidad")]
        if o.get("duracion_min"):
            datos.append(f"{et['duracion']}: {_duracion(o['duracion_min'])}")
        km = o.get("km") or 0
        datos.append(et["a"].format(f"{km:.0f} km" if km >= 1 else f"{round(km * 1000)} m"))
        if o["id"].startswith("bar:") and o.get("horario"):
            datos.append(o["horario"])  # el de OSM tal cual: Mo-Su 12:00-24:00
        linea = f"- **{o['nombre']}** [{o['id']}], " + " · ".join(d for d in datos if d)
        out.append(linea + _detalle(o) + (f". {frases[o['id']]}" if o["id"] in frases else ""))
    return out


def componer(plan: dict, frases: dict[str, str], avisos: list[str]) -> str:
    """El itinerario en markdown: estructura y horas por código; frases del LLM."""
    et = ETIQUETAS[plan["requisitos"]["idioma"]]
    base = plan["base"]["nombre"]
    out = _opciones(plan, frases, et) if "opciones" in plan else []
    for d in plan["dias"]:
        out += ["", "## " + et["dia"].format(d["dia"]) + (f" · {d['fecha']}" if d["fecha"] else "")]
        for i, p in enumerate(d["paradas"]):
            dur = p["duracion_min"] + (planner.PICNIC if p.get("picnic") else 0)
            fin = _mas(p["llegada"], dur)
            linea = f"- **{p['llegada']}–{fin}** **{p['nombre']}** [{p['id']}], {p['municipio']}"
            out.append(linea + _detalle(p) + (f". {frases[p['id']]}" if p["id"] in frases else ""))
            if p.get("picnic"):
                out.append(f"- {et['picnic']}")
            if d["comida"] and i == d["comida_tras"]:
                c, h = d["comida"], p["comida_despues"]
                hasta = _mas(h, planner.COMIDA)
                out.append(f"- **{h}–{hasta}** {et['comida']}: **{c['nombre']}** [{c['id']}]")
        if d["paradas"]:
            fin = et["fin"] if d.get("en_la_base") else et["vuelta"].format(base)
            out.append(f"- **{d['vuelta']}** {fin}")
            if d["vuelta"] < LIBRE_DESDE:
                hasta = planner.HORA_RONDA if d.get("ronda") else planner.HORA_CENA
                out.append(f"- **{d['vuelta']}–{hasta}** {et['libre'].format(base)}")
        if d.get("cena"):
            c = d["cena"]
            out.append(f"- **{c['hora']}–{c['fin']}** {et['cena']}: **{c['nombre']}** [{c['id']}]")
        if d.get("ronda"):
            bares = ", ".join(f"**{b['nombre']}** [{b['id']}]" for b in d["ronda"])
            out.append(f"- **{planner.HORA_RONDA}** {et['pintxos']}: {bares}")
    if plan["alojamientos"]:
        out += ["", "## " + et["alojamientos"]]
        out += [f"- **{a['nombre']}** [{a['id']}], {a['localidad']}" for a in plan["alojamientos"]]
    out += [""] + [f"> {a}" for a in avisos] + ["", f"_{et['nota']}_"]
    return "\n".join(out).strip()


# por llamada (un día: hasta 10 frases y los avisos, ~800). qwen2.5:1.5b entraba en bucle y
# llegaba a 11 000 tokens en un plan (11 min); con tope, el día sale sin frases y se reintenta
MAX_TOKENS = 1200
TIMEOUT_S = 300  # e09 (euskera) dejó colgada la batería esperando a Ollama


def crear_llm(nombre: str = LLM):
    from langchain.chat_models import init_chat_model

    if nombre.startswith("ollama:"):
        # num_ctx: Ollama trae 4096 y recorta en silencio el JSON de los planes largos
        extra = {
            "num_predict": MAX_TOKENS,
            "num_ctx": 12288,  # 8192 no llegaba para una semana con 9 paradas al día
            "client_kwargs": {"timeout": TIMEOUT_S},
        }
    elif nombre.startswith("groq:openai/gpt-oss"):
        # modelos de razonamiento: lo que piensan cuenta en el tope de tokens; con poco
        # razonamiento sobra para extraer requisitos y escribir frases
        extra = {"max_tokens": 2 * MAX_TOKENS, "timeout": TIMEOUT_S, "reasoning_effort": "low"}
    else:
        extra = {"max_tokens": MAX_TOKENS, "timeout": TIMEOUT_S}
    if not nombre.startswith("ollama:"):
        # las APIs gratuitas devuelven 503/429 en picos ("over capacity"): el cliente reintenta
        # con espera exponencial
        extra["max_retries"] = 6
    return init_chat_model(nombre, temperature=0, **extra)


def main() -> None:
    """`navarra-plan "3 días en Estella, románico y naturaleza"`: texto por pantalla y el plan
    en data/plan.json (con el GeoJSON para el mapa)."""
    grafo = construir(crear_llm(), consultas.conectar())
    s = grafo.invoke({"peticion": " ".join(sys.argv[1:])})
    if s.get("pregunta"):
        print(s["pregunta"], f"\n(entendido: {s['requisitos'].model_dump_json()})")
        return
    Path("data/plan.json").write_text(
        json.dumps(s["plan"], ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(s["texto"] + "\n\n(plan completo en data/plan.json)")
