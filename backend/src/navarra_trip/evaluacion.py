"""Evaluación (S4): batería de peticiones, validadores deterministas, juez LLM y embeddings.

    navarra-eval plan [--llm ollama:qwen2.5:7b] [--solo n01,x04]   -> data/eval/<llm>.jsonl
    navarra-eval juez data/eval/<llm>.jsonl                        -> rúbrica 1-5 por plan
    navarra-eval calibrar data/eval/calibracion.csv                -> acuerdo juez / humano
    navarra-eval embeddings                                        -> recall@5 y MRR por modelo
    navarra-eval informe                                           -> data/eval/informe.md

Los resultados se guardan línea a línea: si se corta, la siguiente ejecución sigue donde iba.
"""

import argparse
import csv
import json
import random
import re
import statistics
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from navarra_trip import agente, planner
from navarra_trip.tools import consultas

EVAL = Path(__file__).parents[2] / "eval"  # backend/eval: peticiones y consultas (versionadas)
SALIDA = Path("data/eval")  # resultados (no se versionan)
TABLAS = {
    "mon": "recurso",
    "esp": "recurso",
    "ruta": "recurso",
    "extra": "recurso",
    "bod": "recurso",  # sin él, los planes con bodegas contaban como ids inventados
    "aloj": "alojamiento",
    "rest": "restaurante",
    "bar": "bar",
}
# $ por millón de tokens (entrada, salida). ponytail: a mano; revisar en la web de Anthropic
PRECIOS = {"anthropic:claude-haiku-4-5": (1.0, 5.0)}
MODELOS_EMB = ("intfloat/multilingual-e5-small", "intfloat/multilingual-e5-base")


def leer_csv(nombre: str) -> list[dict]:
    with (EVAL / nombre).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _leer(archivo: Path) -> list[dict]:
    """Una línea por petición; si se repitió con --solo, vale la última."""
    res = {}
    for linea in archivo.open(encoding="utf-8"):
        r = json.loads(linea)
        res[r["id"]] = r
    return list(res.values())


def _archivo(llm: str) -> Path:
    return SALIDA / (re.sub(r"[^\w.-]+", "_", llm) + ".jsonl")


# ---------- ejecución ----------


def ejecutar(grafo, fila: dict) -> dict:
    """Una petición de principio a fin, con tiempo y tokens. Los errores quedan anotados."""
    from langchain_core.callbacks import get_usage_metadata_callback

    out = {"id": fila["id"], "tipo": fila["tipo"], "peticion": fila["peticion"]}
    t0 = time.perf_counter()
    try:
        with get_usage_metadata_callback() as cb:
            s = grafo.invoke({"peticion": fila["peticion"]})
        out["tokens_in"] = sum(u.get("input_tokens", 0) for u in cb.usage_metadata.values())
        out["tokens_out"] = sum(u.get("output_tokens", 0) for u in cb.usage_metadata.values())
        req = s.get("requisitos")
        plan = s.get("plan")
        if plan:  # sin GeoJSON ni geometrías: no hacen falta para evaluar y pesan mucho
            plan = {k: v for k, v in plan.items() if k != "geojson"}
            plan["dias"] = [{k: v for k, v in d.items() if k != "geometria"} for d in plan["dias"]]
        out |= {
            "requisitos": req.model_dump(mode="json") if req else None,
            "pregunta": s.get("pregunta"),
            "plan": plan,
            "texto": s.get("texto"),
            "intentos": s.get("intentos"),
        }
    except (httpx.ConnectError, ConnectionError):
        raise  # OSRM u Ollama apagados: es un fallo del montaje, no un resultado del agente
    except Exception as e:  # una petición que rompe el agente es un resultado, no un fallo
        out["error"] = f"{type(e).__name__}: {e}"
    out["segundos"] = round(time.perf_counter() - t0, 1)
    return out


def bateria(llm_nombre: str, solo: set[str] | None = None) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)
    archivo = _archivo(llm_nombre)
    hechos = set()
    if archivo.exists():
        # las que fallaron por conexión (de versiones anteriores) se repiten
        hechos = {r["id"] for r in _leer(archivo) if "ConnectError" not in r.get("error", "")}
    con = consultas.conectar()
    grafo = agente.construir(agente.crear_llm(llm_nombre), con)
    filas = [f for f in leer_csv("peticiones.csv") if not solo or f["id"] in solo]
    for i, fila in enumerate(filas, 1):
        if fila["id"] in hechos and not solo:
            continue
        try:
            r = ejecutar(grafo, fila) | {"llm": llm_nombre, "fecha": date.today().isoformat()}
        except (httpx.ConnectError, ConnectionError) as e:
            raise SystemExit(
                f"Sin conexión ({e}). ¿Están levantados OSRM (docker compose -f "
                "infra/docker-compose.yml up -d) y Ollama? Vuelve a lanzar: sigue donde iba."
            ) from e
        with archivo.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        estado = r.get("error") or ("pregunta" if r.get("pregunta") else "plan")
        print(f"[{i}/{len(filas)}] {fila['id']} {r['segundos']} s  {estado}")
    return archivo


# ---------- validadores (deterministas) ----------

_N = consultas._sin_tildes
idioma_de = agente.idioma_de


def fecha_esperada(clave: str, hoy: date):
    if clave in ("sabado", "finde"):
        return hoy + timedelta(days=(5 - hoy.weekday()) % 7)
    if clave == "manana":
        return hoy + timedelta(days=1)
    return None


def _fecha_ok(clave: str, fecha: str | None, hoy: date) -> bool | None:
    if not clave:
        return None
    f = date.fromisoformat(fecha) if fecha else None
    if clave == "pasada":
        return f is not None and f < hoy
    if clave == "lejana":
        return f is not None and f > hoy + timedelta(days=planner.DIAS_PREVISION)
    return f == fecha_esperada(clave, hoy)


def _existe(con, id: str) -> bool:
    tabla = TABLAS.get(id.split(":")[0])
    return bool(tabla) and bool(con.execute(f"SELECT 1 FROM {tabla} WHERE id = ?", [id]).fetchone())


def validar(fila: dict, r: dict, con, hoy: date, sim=None) -> dict:
    """Comprobaciones con código. None = no aplica a esta petición."""
    v: dict = {"error": "error" in r}
    req, plan, pregunta = r.get("requisitos") or {}, r.get("plan"), r.get("pregunta")
    base = (plan or {}).get("base", {}).get("nombre") or req.get("base") or ""
    v["dias_ok"] = req.get("dias") == int(fila["dias"]) if fila["dias"] else None
    v["base_ok"] = _N(fila["base"]) in _N(base) if fila["base"] else None
    v["transporte_ok"] = req.get("transporte") == fila["transporte"] if fila["transporte"] else None
    v["ritmo_ok"] = req.get("ritmo") == fila["ritmo"] if fila["ritmo"] else None
    v["idioma_ok"] = req.get("idioma") == fila["idioma"] if req else False
    v["fecha_ok"] = _fecha_ok(fila["fecha"], req.get("fecha_inicio"), hoy)

    esperado = fila["esperado"]
    v["comportamiento_ok"] = {
        "plan": bool(plan) and not pregunta,
        "pregunta": bool(pregunta),
        "pregunta_dias": bool(pregunta) and not req.get("dias"),
        "pregunta_base": bool(pregunta),
        "plan_con_aviso": bool(plan) and bool(plan.get("avisos")),
        "plan_sin_inventar": bool(plan),
        "opciones": bool(plan) and "opciones" in plan and not pregunta,
    }[esperado]
    if not plan:
        return v

    texto = r.get("texto") or ""
    citados = set(agente.CITA.findall(texto))
    v["ids_ok"] = citados <= planner.ids(plan) and all(_existe(con, i) for i in citados)
    v["paradas_citadas"] = all(p["id"] in citados for d in plan["dias"] for p in d["paradas"])
    ritmo = req.get("ritmo", "normal")
    dias = plan["dias"]
    v["trayecto_ok"] = all(d["trayecto_min"] <= planner.MAX_TRAYECTO[ritmo] for d in dias)
    fin = planner._hora(planner.FIN[ritmo])
    v["horario_ok"] = all((d.get("vuelta") or "00:00") <= fin for d in dias)
    v["paradas_ok"] = all(len(d["paradas"]) <= planner.PARADAS[ritmo] for d in dias)
    todas = [p["id"] for d in dias for p in d["paradas"]]
    v["sin_repetidas"] = len(todas) == len(set(todas))
    v["dias_vacios"] = sum(not d["paradas"] for d in dias)
    if req.get("transporte") == "pie":
        b = plan["base"]
        v["pie_ok"] = all(
            con.execute(
                "SELECT km(?, ?, ?, ?)", [b["lat"], b["lon"], p["lat"], p["lon"]]
            ).fetchone()[0]
            <= planner.RADIO_KM["pie"] + 0.5
            for d in dias
            for p in d["paradas"]
        )
    v["paradas_por_dia"] = round(len(todas) / max(len(dias), 1), 2)
    intereses = [i for i in fila["intereses"].split("|") if i]
    if intereses and sim:
        cubiertos = 0
        for i in intereses:
            ranking = sorted(sim(i).items(), key=lambda x: -x[1])[:15]
            cubiertos += any(id in set(todas) for id, _ in ranking)
        v["cobertura_intereses"] = round(cubiertos / len(intereses), 2)
    v["idioma_texto_ok"] = idioma_de(texto) == fila["idioma"]
    return v


# ---------- juez LLM ----------


class Rubrica(BaseModel):
    utilidad: int = Field(ge=1, le=5, description="¿Responde a lo pedido (días, intereses, ritmo)?")
    coherencia: int = Field(ge=1, le=5, description="¿Orden, horas y geografía tienen sentido?")
    fidelidad: int = Field(ge=1, le=5, description="¿Se ciñe a los datos, sin inventar nada?")
    redaccion: int = Field(ge=1, le=5, description="¿Claro, natural y en el idioma pedido?")
    comentario: str = Field(description="Una frase con el principal fallo, o 'ninguno'")


# Los ejemplos de qué restar salen de la calibración (03-10-2026, 20 planes a mano): sin ellos el
# juez daba de media +1,15 en fidelidad y +1 en coherencia.
JUEZ = """Evalúas itinerarios turísticos por Navarra generados por un asistente.
Recibes la petición, los datos del plan (JSON, la única fuente válida) y el texto final.
Puntúa de 1 (muy mal) a 5 (excelente) cada criterio. Sé exigente: un 5 es raro.
- utilidad: cubre los días, intereses y ritmo pedidos; la tarde no queda vacía sin motivo.
- coherencia: orden y horas lógicos. Resta si el texto pone algo después de la cena (p. ej. la
  vuelta a la base), si una hora retrocede, si va y vuelve entre pueblos sin necesidad o si
  junta caminatas largas el mismo día.
- fidelidad: todo lo que dice el texto está en el JSON. Resta por cada hora, duración, lugar,
  precio o dato que no esté (p. ej. una hora de llegada al alojamiento o del picnic inventada).
- redaccion: claro, natural, en el idioma de la petición."""


def juzgar(archivo: Path, llm_nombre: str = "ollama:qwen2.5:7b") -> None:
    llm = agente.crear_llm(llm_nombre).with_structured_output(Rubrica)
    lineas = _leer(archivo)
    for r in lineas:
        if r.get("plan") and "juez" not in r:
            datos = json.dumps(agente._para_llm(r["plan"]), ensure_ascii=False)
            msgs = [
                ("system", JUEZ),
                ("human", f"PETICIÓN: {r['peticion']}\n\nJSON: {datos}\n\nTEXTO:\n{r['texto']}"),
            ]
            try:
                r["juez"] = llm.invoke(msgs).model_dump() | {"modelo": llm_nombre}
            except Exception as e:
                r["juez"] = {"error": str(e)}
            print(r["id"], r["juez"])
    archivo.write_text(
        "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in lineas),
        encoding="utf-8",
    )
    con_plan = [r for r in lineas if r.get("juez") and "error" not in r["juez"]]
    muestra = random.Random(0).sample(con_plan, min(20, len(con_plan)))
    destino = SALIDA / "calibracion.csv"
    with destino.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        crit = ["utilidad", "coherencia", "fidelidad", "redaccion"]
        w.writerow(
            ["id", "peticion", "texto", *[f"juez_{c}" for c in crit], *[f"tu_{c}" for c in crit]]
        )
        for r in muestra:
            w.writerow(
                [r["id"], r["peticion"], r["texto"], *[r["juez"][c] for c in crit], *[""] * 4]
            )
    print(
        f"Puntúa tú (1-5) las columnas tu_* de {destino} y ejecuta: navarra-eval calibrar {destino}"
    )


def calibrar(archivo: Path) -> None:
    with archivo.open(encoding="utf-8") as f:
        filas = [r for r in csv.DictReader(f) if r["tu_utilidad"]]
    for c in ("utilidad", "coherencia", "fidelidad", "redaccion"):
        dif = [abs(int(r[f"juez_{c}"]) - int(r[f"tu_{c}"])) for r in filas]
        if dif:
            print(
                f"{c:11} n={len(dif)}  diferencia media {statistics.mean(dif):.2f}  "
                f"a ±1: {sum(d <= 1 for d in dif) / len(dif):.0%}"
            )


# ---------- embeddings ----------


def embeddings() -> str:
    from navarra_trip.tools import semantica

    con = consultas.conectar()
    consultas_ = leer_csv("consultas_semanticas.csv")
    filas = ["| Modelo | recall@5 | MRR |", "|---|---|---|"]
    for modelo in MODELOS_EMB:
        emb, tabla = semantica.embebedor(modelo), semantica.tabla_de(modelo)
        semantica.indexar(con, emb, tabla=tabla)
        rec, rr = [], []
        for c in consultas_:
            relevantes = set(c["relevantes"].split("|"))
            hits = [
                h["id"]
                for h in semantica.buscar_semantica(con, c["consulta"], 10, emb=emb, tabla=tabla)
            ]
            rec.append(len(relevantes & set(hits[:5])) / min(5, len(relevantes)))
            rr.append(next((1 / (i + 1) for i, h in enumerate(hits) if h in relevantes), 0))
        nombre = modelo.rsplit("/", 1)[-1]
        filas.append(f"| {nombre} | {statistics.mean(rec):.2f} | {statistics.mean(rr):.2f} |")
    tabla_md = "\n".join(filas)
    print(tabla_md)
    return tabla_md


# ---------- informe ----------


def _pct(xs) -> str:
    xs = [x for x in xs if x is not None]
    return f"{sum(map(bool, xs)) / len(xs):.0%} ({len(xs)})" if xs else "–"


def informe() -> str:
    con = consultas.conectar()
    sim = planner.similitud_por_defecto(con)
    peticiones = {f["id"]: f for f in leer_csv("peticiones.csv")}
    partes = ["# Evaluación de navarra-trip-agent", ""]
    for archivo in sorted(SALIDA.glob("*.jsonl")):
        res = _leer(archivo)
        if not res:
            continue
        llm = res[0].get("llm", archivo.stem)
        partes += [f"## {llm} · {archivo.name} ({len(res)} peticiones, {res[0].get('fecha')})", ""]
        for grupo, tipos in (
            ("Normales", {"normal"}),
            ("Imposibles y ambiguas", {"imposible", "ambigua"}),
            ("Euskera", {"euskera"}),
            ("Opciones (sin itinerario)", {"opciones"}),
        ):
            rs = [r for r in res if r["tipo"] in tipos]
            if not rs:
                continue
            vs = [
                validar(peticiones[r["id"]], r, con, date.fromisoformat(r["fecha"]), sim)
                for r in rs
            ]

            def pct(*ks, vs=vs):
                return " · ".join(_pct([v.get(k) for v in vs]) for k in ks)

            seg = [r["segundos"] for r in rs]
            vacios = [v["dias_vacios"] > 0 for v in vs if "dias_vacios" in v]
            tokens = " / ".join(
                _media([r.get(k) for r in rs], 0) for k in ("tokens_in", "tokens_out")
            )
            metricas = [
                ("Comportamiento esperado (plan / pregunta / aviso)", pct("comportamiento_ok")),
                (
                    "Interpretación: días · base · transporte · ritmo",
                    pct("dias_ok", "base_ok", "transporte_ok", "ritmo_ok"),
                ),
                ("Interpretación: idioma · fecha", pct("idioma_ok", "fecha_ok")),
                ("**Ids citados reales**", pct("ids_ok")),
                ("Todas las paradas citadas", pct("paradas_citadas")),
                (
                    "Trayecto · horario · n.º de paradas · sin repetidas",
                    pct("trayecto_ok", "horario_ok", "paradas_ok", "sin_repetidas"),
                ),
                ("A pie dentro del radio", pct("pie_ok")),
                ("Planes con algún día vacío", _pct(vacios)),
                (
                    "Cobertura media de intereses",
                    _media([v.get("cobertura_intereses") for v in vs]),
                ),
                ("Idioma del texto correcto", pct("idioma_texto_ok")),
                ("Errores del agente", pct("error")),
                ("Latencia media / máx. (s)", f"{statistics.mean(seg):.0f} / {max(seg):.0f}"),
                ("Tokens medios (entrada / salida)", tokens),
            ]
            partes += [f"### {grupo} ({len(rs)})", "", "| Métrica | Valor |", "|---|---|"]
            partes += [f"| {m} | {x} |" for m, x in metricas]
            if llm in PRECIOS:
                pin, pout = PRECIOS[llm]
                coste = (
                    sum(r.get("tokens_in", 0) * pin + r.get("tokens_out", 0) * pout for r in rs)
                    / 1e6
                )
                partes.append(f"| Coste por plan (USD, aprox.) | {coste / len(rs):.4f} |")
            jueces = [r["juez"] for r in rs if r.get("juez") and "error" not in r["juez"]]
            if jueces:
                partes.append(
                    "| Juez (utilidad · coherencia · fidelidad · redacción) | "
                    + " · ".join(
                        _media([j[c] for j in jueces], 1)
                        for c in ("utilidad", "coherencia", "fidelidad", "redaccion")
                    )
                    + " |"
                )
            partes.append("")
    texto = "\n".join(partes)
    (SALIDA / "informe.md").write_text(texto, encoding="utf-8")
    print(texto)
    return texto


def _media(xs, dec: int = 2) -> str:
    xs = [x for x in xs if x is not None]
    return f"{statistics.mean(xs):.{dec}f}" if xs else "–"


def main() -> None:
    p = argparse.ArgumentParser(prog="navarra-eval")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan")
    a.add_argument("--llm", default=agente.LLM)
    a.add_argument("--solo", help="ids separados por comas (se repiten aunque ya estén)")
    j = sub.add_parser("juez")
    j.add_argument("archivo", type=Path)
    j.add_argument("--llm", default="ollama:qwen2.5:7b")
    c = sub.add_parser("calibrar")
    c.add_argument("archivo", type=Path)
    sub.add_parser("embeddings")
    sub.add_parser("informe")
    args = p.parse_args()
    if args.cmd == "plan":
        bateria(args.llm, set(args.solo.split(",")) if args.solo else None)
    elif args.cmd == "juez":
        juzgar(args.archivo, args.llm)
    elif args.cmd == "calibrar":
        calibrar(args.archivo)
    elif args.cmd == "embeddings":
        embeddings()
    else:
        informe()
