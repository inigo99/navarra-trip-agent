import duckdb

from navarra_trip.ingest.normaliza import establecimientos, fuera_de_navarra, guardar, recursos

FUENTE = {"url_fuente": "https://datosabiertos.navarra.es/es/dataset/x", "licencia": "CC-BY-4.0"}
CUADRADO = {"type": "Polygon", "coordinates": [[[-3, 41], [0, 41], [0, 44], [-3, 44], [-3, 41]]]}


def _rec(cod, nombre, tipo, x, y, **extra):
    return {
        "Codrecurso": cod,
        "Nombre": nombre,
        "NombreLocalidad": "PAMPLONA",
        "DescripZona": "Cuenca de Pamplona",
        "GEORR_X": x,
        "GEORR_Y": y,
        **tipo,
        **extra,
    }


MON = FUENTE | {
    "registros": [
        _rec(
            "2859",
            "Acueducto de Noáin",
            {"Tipo": "Obras de ingeniería"},
            "612270",
            "4733591",
            ESTILO="Moderno",
        )
    ]
}
ESP = FUENTE | {
    "registros": [
        _rec("3168", "Urbasa-Andía", {"TIPO": "Montes y sierras"}, "573295", "4742309"),
        _rec("3168", "Urbasa-Andía", {"TIPO": "Parques naturales"}, "573295", "4742309"),
        _rec("9999", "Cascada del Cubo", {"TIPO": "Cascadas"}, "0", "0"),
    ]
}


def _est(cod, **extra):
    base = {
        "COD_INSCRIPCION": cod,
        "NOMBRE": "Europa",
        "CATEGORIA": "3 Estrellas",
        "DIRECCION": "Espoz y Mina 11",
        "LOCALIDAD": "Pamplona / Iruña",
        "MUNICIPIO": "Pamplona / Iruña",
        "SUB_ZONA": "Pamplona y Comarca",
    }
    return base | extra


ALOJ = FUENTE | {
    "registros": [_est("UH000003", MODALIDAD="Hotel", PLAZAS="38")] * 3
    + [_est("uat1", MODALIDAD="Casa rural vivienda", PLAZAS="6")]
}
REST = FUENTE | {
    "registros": [_est("UR1", Especialidad="Desconocido"), _est("ur1", Especialidad="Asador")]
}


class GeoFalso:
    def geocodificar(self, direccion, localidad, municipio):
        return (-1.64, 42.81, "direccion")


def test_recursos():
    filas = {f["id"]: f for f in recursos(MON, ESP)}
    assert set(filas) == {"mon:2859", "esp:3168", "esp:9999"}
    assert filas["esp:3168"]["subcategorias"] == ["Montes y sierras", "Parques naturales"]
    assert filas["esp:9999"]["lon"] is None  # (0, 0) no se convierte en un punto inventado
    m = filas["mon:2859"]
    assert round(m["lon"], 2) == -1.63 and round(m["lat"], 2) == 42.75  # UTM 30N -> WGS84 (Noáin)
    assert m["municipio"] == "Pamplona" and m["licencia"] == "CC-BY-4.0"


def test_establecimientos_sin_duplicados_y_tipados():
    aloj = establecimientos(ALOJ, "aloj", GeoFalso())
    assert [a["id"] for a in aloj] == ["aloj:UH000003", "aloj:UAT1"]
    assert aloj[0]["tipo"] == "hotel" and aloj[0]["plazas"] == 38 and aloj[1]["tipo"] == "rural"
    rest = establecimientos(REST, "rest", GeoFalso())
    assert len(rest) == 1 and rest[0]["especialidad"] is None  # 'Desconocido' -> NULL; UR1 == ur1


def test_guardar_en_duckdb_con_clave_primaria():
    con = duckdb.connect()
    guardar(con, "recurso", recursos(MON, ESP))
    assert con.sql("SELECT count(*), count(lon) FROM recurso").fetchone() == (3, 2)
    assert con.sql("SELECT subcategorias FROM recurso WHERE id = 'esp:3168'").fetchone()[0] == [
        "Montes y sierras",
        "Parques naturales",
    ]


def test_fuera_de_navarra():
    filas = [
        {"id": "a", "lon": -1.6, "lat": 42.8},
        {"id": "b", "lon": 2.1, "lat": 41.4},
        {"id": "c", "lon": None, "lat": None},
    ]
    assert fuera_de_navarra(filas, CUADRADO) == ["b"]
