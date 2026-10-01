import duckdb

from navarra_trip.ingest.normaliza import guardar
from navarra_trip.tools.consultas import preparar
from navarra_trip.tools.semantica import buscar_semantica, indexar, texto_de

TEMAS = ("castillo", "cascada", "iglesia")


def emb_falso(textos, tipo):
    """Un eje por tema: basta para comprobar el flujo sin descargar el modelo."""
    return [[1.0 if t in x.lower() else 0.01 for t in TEMAS] for x in textos]


def _rec(id, nombre, cat, sub, desc):
    return {
        "id": id,
        "nombre": nombre,
        "categoria": cat,
        "subcategorias": sub,
        "estilo": None,
        "municipio": "X",
        "zona": None,
        "lat": 42.5,
        "lon": -1.6,
        "descripcion": desc,
        "descripcion_fuente": "wikipedia",
        "horario": None,
        "visitantes_12m": None,
        "url_fuente": "u",
    }


def _con():
    con = preparar(duckdb.connect())
    guardar(
        con,
        "recurso",
        [
            _rec(
                "mon:1",
                "Palacio Real de Olite",
                "monumento",
                ["Castillos/Palacios"],
                "Un castillo gótico.",
            ),
            _rec(
                "esp:1",
                "Nacedero del Urederra",
                "natural",
                ["Ríos"],
                "Una cascada de agua turquesa.",
            ),
            _rec(
                "mon:2",
                "Santa María de Eunate",
                "monumento",
                ["Iglesias y ermitas"],
                "Iglesia románica.",
            )
            | {"estilo": "Románico"},
        ],
    )
    return con


def test_indexar_y_buscar(tmp_path):
    con = _con()
    assert indexar(con, emb_falso, tmp_path) == 3
    r = buscar_semantica(con, "una cascada", k=2, emb=emb_falso, ruta=tmp_path)
    assert r[0]["id"] == "esp:1" and r[0]["similitud"] > 0.9
    assert r[0]["nombre"] == "Nacedero del Urederra"  # datos completos desde DuckDB


def test_filtro_por_categoria(tmp_path):
    con = _con()
    indexar(con, emb_falso, tmp_path)
    r = buscar_semantica(
        con, "una cascada", k=3, categoria="monumento", emb=emb_falso, ruta=tmp_path
    )
    assert {x["id"] for x in r} == {"mon:1", "mon:2"}


def test_el_estilo_se_indexa(tmp_path):
    con = _con()
    textos = []
    indexar(con, lambda t, tipo: textos.extend(t) or emb_falso(t, tipo), tmp_path)
    assert any("Estilo Románico" in t for t in textos)


def test_texto_de_incluye_tipo_estilo_y_descripcion():
    r = _rec("a", "Eunate", "monumento", ["Iglesias y ermitas"], "Planta octogonal.")
    assert texto_de(r) == "Eunate. Iglesias y ermitas. X Planta octogonal."
    r["estilo"] = "Románico"
    assert texto_de(r) == "Eunate. Iglesias y ermitas. Estilo Románico. X Planta octogonal."
