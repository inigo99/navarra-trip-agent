"""Búsqueda por significado sobre los recursos (multilingual-e5 en ONNX + índice numpy).

"castillos medievales" o "sitios con agua para ir con niños" encuentran recursos aunque no
compartan palabras. El índice se construye con `navarra-indexar`; el modelo (int8, ~280 MB) se
descarga la primera vez a data/modelos.

Antes era sentence-transformers + LanceDB: torch y pyarrow no caben en una función de Vercel
(500 MB) y con ~200 vectores una búsqueda exacta en numpy tarda menos de 1 ms.
"""

import os
from collections.abc import Callable
from functools import cache
from pathlib import Path

import duckdb
import httpx
import numpy as np

from navarra_trip.tools.consultas import _COLS_RECURSO, _filas, conectar

# base (768 dim., ~280 M parámetros) frente a small en `navarra-eval embeddings` (218 lugares):
# recall@5 0,64 frente a 0,56 y MRR 0,87 frente a 0,75. Cada modelo tiene su índice.
MODELO = os.environ.get("NAVARRA_EMBEDDINGS", "intfloat/multilingual-e5-base")
# exportaciones ONNX de los mismos pesos (transformers.js)
ONNX = {
    "intfloat/multilingual-e5-base": "Xenova/multilingual-e5-base",
    "intfloat/multilingual-e5-small": "Xenova/multilingual-e5-small",
}
# Precisión: quantized (int8, 280 MB en base), fp16 (555 MB) o fp32 (1,1 GB). El índice se hace
# una vez en local, en fp32; la consulta, en fp16, que da lo mismo (0,64 / 0,87) con la mitad de
# memoria. int8 en la consulta bajaba a 0,62 / 0,83 y en las dos, a 0,58 / 0,78.
VARIANTE = os.environ.get("NAVARRA_ONNX", "fp16")  # consultas
VARIANTE_INDICE = os.environ.get("NAVARRA_ONNX_INDICE", "fp32")
MODELOS = Path("data/modelos")
DATOS = Path("data")
MAX_TOKENS = 512  # el de e5


def _nombre(modelo: str) -> str:
    return modelo.rsplit("/", 1)[-1]


def indice_de(modelo: str, carpeta: Path = DATOS) -> Path:
    return carpeta / f"indice_{_nombre(modelo)}.npz"


INDICE = indice_de(MODELO)

Embebedor = Callable[[list[str], str], np.ndarray]


def descargar(modelo: str = MODELO, variante: str = VARIANTE) -> Path:
    """Tokenizador y modelo ONNX de Hugging Face, si no están ya (también en el build de Vercel)."""
    destino = MODELOS / _nombre(modelo)
    onnx = "onnx/model.onnx" if variante == "fp32" else f"onnx/model_{variante}.onnx"
    for local, remoto in {"tokenizer.json": "tokenizer.json", f"{variante}.onnx": onnx}.items():
        f = destino / local
        if f.exists():
            continue
        destino.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{ONNX[modelo]}/resolve/main/{remoto}"
        tmp = f.with_suffix(".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with tmp.open("wb") as out:
                for trozo in r.iter_bytes(1 << 20):
                    out.write(trozo)
        tmp.rename(f)  # sin .part a medias si se corta
    return destino


@cache
def _modelo(modelo: str, variante: str):
    import onnxruntime as ort
    from tokenizers import Tokenizer

    carpeta = descargar(modelo, variante)
    tok = Tokenizer.from_file(str(carpeta / "tokenizer.json"))
    tok.enable_truncation(MAX_TOKENS)
    tok.enable_padding(pad_id=tok.token_to_id("<pad>") or 0, pad_token="<pad>")
    opciones = ort.SessionOptions()
    opciones.intra_op_num_threads = os.cpu_count() or 1
    sesion = ort.InferenceSession(str(carpeta / f"{variante}.onnx"), opciones)
    return tok, sesion, {i.name for i in sesion.get_inputs()}


def embebedor(modelo: str, variante: str = VARIANTE, lote: int = 32) -> Embebedor:
    """tipo: 'query' o 'passage' (e5 se entrenó con esos prefijos). Media de los tokens según
    la máscara y normalizado, como sentence-transformers con e5."""

    def emb(textos: list[str], tipo: str) -> np.ndarray:
        tok, sesion, entradas = _modelo(modelo, variante)
        out = []
        for i in range(0, len(textos), lote):
            cod = tok.encode_batch([f"{tipo}: {t}" for t in textos[i : i + lote]])
            ids = np.array([c.ids for c in cod], dtype=np.int64)
            mascara = np.array([c.attention_mask for c in cod], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mascara}
            if "token_type_ids" in entradas:
                feed["token_type_ids"] = np.zeros_like(ids)
            ocultos = sesion.run(None, {k: v for k, v in feed.items() if k in entradas})[0]
            m = mascara[..., None].astype(np.float32)
            medias = (ocultos * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            out.append(medias / np.linalg.norm(medias, axis=1, keepdims=True))
        return np.concatenate(out) if out else np.zeros((0, 0), np.float32)

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
    con: duckdb.DuckDBPyConnection, emb: Embebedor | None = None, ruta: Path = INDICE
) -> int:
    emb = emb or embebedor(MODELO, VARIANTE_INDICE)
    filas = _filas(
        con,
        "SELECT id, nombre, categoria, subcategorias, estilo, municipio, descripcion FROM recurso",
        [],
    )
    vecs = np.asarray(emb([texto_de(f) for f in filas], "passage"), dtype=np.float32)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        ruta,
        ids=np.array([f["id"] for f in filas]),
        categorias=np.array([f["categoria"] for f in filas]),
        vectores=vecs,
    )
    _indice.cache_clear()
    return len(filas)


@cache
def _indice(ruta: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with np.load(ruta) as z:  # FileNotFoundError sin índice: el planner usa palabras
        return z["ids"], z["categorias"], z["vectores"]


def buscar_semantica(
    con: duckdb.DuckDBPyConnection,
    texto: str,
    k: int = 10,
    categoria: str | None = None,
    emb: Embebedor = embeber,
    ruta: Path = INDICE,
) -> list[dict]:
    """Los k recursos más parecidos a `texto`, con su similitud (coseno; vectores normalizados)."""
    ids, cats, vecs = _indice(ruta)
    q = np.asarray(emb([texto], "query"), dtype=np.float32)[0]
    q = q / (np.linalg.norm(q) or 1)
    sims = vecs @ q / np.clip(np.linalg.norm(vecs, axis=1), 1e-9, None)
    if categoria:
        sims = np.where(cats == categoria, sims, -np.inf)
    orden = [i for i in np.argsort(-sims)[:k] if np.isfinite(sims[i])]
    if not orden:
        return []
    elegidos = [str(ids[i]) for i in orden]
    filas = {
        f["id"]: f
        for f in _filas(
            con, f"SELECT {_COLS_RECURSO} FROM recurso WHERE list_contains(?, id)", [elegidos]
        )
    }
    return [
        filas[id] | {"similitud": round(float(sims[i]), 3)}
        for id, i in zip(elegidos, orden, strict=True)
        if id in filas
    ]


def main() -> None:
    """`navarra-indexar`: embeddings de los recursos (data/indice_*.npz) y 3 búsquedas de prueba."""
    con = conectar()
    n = indexar(con)
    print(f"{n} recursos indexados en {INDICE} ({MODELO})")
    print(f"ONNX: índice {VARIANTE_INDICE}, consultas {VARIANTE}")
    for consulta in ("castillos medievales", "cascadas y ríos", "arte románico"):
        res = buscar_semantica(con, consulta, k=5)
        print(f"\n{consulta}:\n" + "\n".join(f"  {r['similitud']:.3f} {r['nombre']}" for r in res))
