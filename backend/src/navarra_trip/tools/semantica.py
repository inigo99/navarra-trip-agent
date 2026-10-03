"""Búsqueda por significado sobre los recursos (multilingual-e5 + LanceDB).

"castillos medievales" o "sitios con agua para ir con niños" encuentran recursos aunque no
compartan palabras. El índice se construye con `navarra-indexar` (necesita el extra
`semantica`: `uv sync --extra semantica`); buscar solo necesita el modelo para la consulta.
"""

import os
from collections.abc import Callable
from functools import cache
from pathlib import Path

import duckdb
import lancedb

from navarra_trip.tools.consultas import _COLS_RECURSO, _filas, conectar

# base (768 dim., ~280 M parámetros) frente a small en `navarra-eval embeddings`: recall@5 0,70
# frente a 0,59 y MRR 0,90 frente a 0,83. Cada modelo tiene su tabla: consulta e índice casan.
MODELO = os.environ.get("NAVARRA_EMBEDDINGS", "intfloat/multilingual-e5-base")
LANCE = Path("data/lancedb")


def tabla_de(modelo: str) -> str:
    return "recursos_" + modelo.rsplit("/", 1)[-1]


TABLA = tabla_de(MODELO)

Embebedor = Callable[[list[str], str], list[list[float]]]


@cache
def _modelo(nombre: str = MODELO):
    from sentence_transformers import SentenceTransformer  # extra opcional: import tardío

    return SentenceTransformer(nombre)


def embebedor(modelo: str) -> Embebedor:
    """tipo: 'query' o 'passage' (e5 se entrenó con esos prefijos)."""

    def emb(textos: list[str], tipo: str) -> list[list[float]]:
        vecs = _modelo(modelo).encode([f"{tipo}: {t}" for t in textos], normalize_embeddings=True)
        return vecs.tolist()

    return emb


embeber = embebedor(MODELO)


def texto_de(r: dict) -> str:
    """Nombre, tipo y estilo delante: "arte románico" fallaba sin el estilo, que es lo que
    mejor lo identifica y a menudo no sale en la descripción."""
    partes = [r["nombre"] + ".", ", ".join(r["subcategorias"]) + "."]
    if r.get("estilo"):
        partes.append(f"Estilo {r['estilo']}.")
    partes += [r["municipio"], r["descripcion"]]
    return " ".join(p for p in partes if p)


def indexar(
    con: duckdb.DuckDBPyConnection,
    emb: Embebedor = embeber,
    ruta: Path = LANCE,
    tabla: str = TABLA,
) -> int:
    filas = _filas(
        con,
        "SELECT id, nombre, categoria, subcategorias, estilo, municipio, descripcion FROM recurso",
        [],
    )
    vecs = emb([texto_de(f) for f in filas], "passage")
    datos = [
        {"id": f["id"], "categoria": f["categoria"], "vector": v}
        for f, v in zip(filas, vecs, strict=True)
    ]
    lancedb.connect(ruta).create_table(tabla, data=datos, mode="overwrite")
    return len(datos)


def buscar_semantica(
    con: duckdb.DuckDBPyConnection,
    texto: str,
    k: int = 10,
    categoria: str | None = None,
    emb: Embebedor = embeber,
    ruta: Path = LANCE,
    tabla: str = TABLA,
) -> list[dict]:
    """Los k recursos más parecidos a `texto`, con su similitud (coseno, 0-1)."""
    q = lancedb.connect(ruta).open_table(tabla).search(emb([texto], "query")[0])
    q = q.distance_type("cosine")
    if categoria:
        q = q.where(f"categoria = '{categoria.replace(chr(39), '')}'", prefilter=True)
    hits = q.limit(k).to_list()
    if not hits:
        return []
    ids = [h["id"] for h in hits]
    filas = {
        f["id"]: f
        for f in _filas(
            con, f"SELECT {_COLS_RECURSO} FROM recurso WHERE list_contains(?, id)", [ids]
        )
    }
    return [filas[h["id"]] | {"similitud": round(1 - h["_distance"], 3)} for h in hits]


def main() -> None:
    """`navarra-indexar`: embeddings de los recursos en data/lancedb y 3 búsquedas de prueba."""
    con = conectar()
    print(f"{indexar(con)} recursos indexados en {LANCE} ({MODELO})")
    for consulta in ("castillos medievales", "cascadas y ríos", "arte románico"):
        res = buscar_semantica(con, consulta, k=5)
        print(f"\n{consulta}:\n" + "\n".join(f"  {r['similitud']:.3f} {r['nombre']}" for r in res))
