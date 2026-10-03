from navarra_trip.ingest import senderos


def _f(tramo, coords, **p):
    props = {
        "IDRUTA": "SL-NA 12",
        "TRAMO": tramo,
        "NOMBRE": "Xorroxin",
        "LUGARINI": "Erratzu",
        "LUGARFIN": "Erratzu",
        "ALTITUDMAX": 378,
        "DESNIPOSI": 166,
        "DESNINEGA": 166,
        "URL": "https://senderos.nafarmendi.org/ruta/ver/130",
        "DESCARGA": "https://senderos.nafarmendi.org/docs/gpx/130.gpx",
        "GEOM_LONG": 6899.98,
    } | p
    return {"properties": props, "geometry": {"type": "MultiLineString", "coordinates": [coords]}}


LINEA = [[-1.459, 43.183], [-1.452, 43.170], [-1.459, 43.183]]


def test_mide():
    assert senderos.minutos(6.9, 166, 166) == 120  # 1 h 43 llano + la mitad de 42 min
    assert senderos.minutos(4, 1000, 1000) == 285  # 4 h 10 de desnivel + la mitad de 1 h


def test_ruta_circular_y_lineal():
    variante = _f("SL-NA 12.1", LINEA[:2], GEOM_LONG=900, LUGARFIN="Elizondo")
    r = senderos.ruta("DOTACI_Lin_SLNA12", [variante, _f("SL-NA 12", LINEA)])
    assert r["id"] == "ruta:slna12" and r["categoria"] == "ruta" and r["circular"]
    assert (r["lon"], r["lat"]) == (-1.459, 43.183) and r["longitud_km"] == 6.9
    lineal = senderos.ruta("DOTACI_Lin_PRNA1", [_f("SL-NA 12", LINEA, LUGARFIN="Elizondo")])
    assert lineal["longitud_km"] == 13.8 and lineal["duracion_min"] > r["duracion_min"]
    assert senderos.ruta("DOTACI_Lin_SLNA9", [_f("SL-NA 12", LINEA, GEOM_LONG=40000)]) is None


def test_cimas_cerca_del_trazado():
    r = senderos.ruta("DOTACI_Lin_SLNA12", [_f("SL-NA 12", LINEA)])
    picos = [
        {"lon": -1.4521, "lat": 43.1701, "tags": {"name": "Alto", "ele": "378"}},
        {"lon": -1.40, "lat": 43.10, "tags": {"name": "Lejos", "ele": "900"}},
    ]
    senderos.cimas_cerca([r], picos)
    assert r["cimas"] == "Alto (378 m)" and "Cimas" in r["subcategorias"]
    assert "Pasa por la cima de Alto (378 m)" in senderos.ficha(r)


def test_capa_con_otros_campos():
    f = {
        "properties": {
            "IDRUTA": "PR-NA 210",
            "RUTA": "Egaren bira",
            "TRAMO": "PR-NA 210 Egaren bira",
            "URL": "u",
            "DESCARGA": "g",
            "GEOM_LONG": 13264.46,
        },
        "geometry": {"type": "MultiLineString", "coordinates": [LINEA]},
    }
    r = senderos.ruta("DOTACI_Lin_PRNA210", [f])
    assert r["nombre"] == "Egaren bira (PR-NA 210)" and r["desnivel_m"] is None
    senderos.cimas_cerca([r], [])
    t = senderos.ficha(r)
    assert "desnivel" not in t and "None" not in t and "13.3 km" in t
