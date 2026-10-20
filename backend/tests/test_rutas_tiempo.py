import httpx

from navarra_trip.tools.rutas import matriz, ruta
from navarra_trip.tools.tiempo import prevision

PAMPLONA, OLITE, UJUE = (42.8125, -1.6432), (42.4797, -1.6498), (42.5031, -1.5007)


def test_ruta_convierte_unidades_y_orden_de_coordenadas():
    def api(req):
        assert req.url.path == "/route/v1/driving/-1.6432,42.8125;-1.6498,42.4797;-1.5007,42.5031"
        legs = [{"distance": 42561.6, "duration": 2080.4}, {"distance": 18000, "duration": 1500}]
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [{"distance": 60561.6, "duration": 3580.4, "legs": legs}],
            },
        )

    r = ruta([PAMPLONA, OLITE, UJUE], client=httpx.Client(transport=httpx.MockTransport(api)))
    assert r == {
        "km": 60.6,
        "minutos": 60,
        "tramos": [{"km": 42.6, "minutos": 35}, {"km": 18.0, "minutos": 25}],
    }


def test_matriz_a_pie_y_sin_camino():
    def api(req):
        assert req.url.host == "localhost" and req.url.port == 5001
        return httpx.Response(200, json={"code": "Ok", "durations": [[0, 32787.2], [None, 0]]})

    m = matriz(
        [PAMPLONA, OLITE], modo="pie", client=httpx.Client(transport=httpx.MockTransport(api))
    )
    assert m == [[0.0, 546.5], [None, 0.0]]


def test_prevision_marca_mal_tiempo():
    daily = {
        "time": ["2026-10-02", "2026-10-03"],
        "weather_code": [1, 63],
        "temperature_2m_max": [22.1, 15.0],
        "temperature_2m_min": [9.0, 10.2],
        "precipitation_sum": [0.0, 12.4],
        "precipitation_probability_max": [5, 90],
    }

    def api(req):
        assert req.url.params["timezone"] == "Europe/Madrid"
        return httpx.Response(200, json={"daily": daily})

    p = prevision(*OLITE, dias=2, client=httpx.Client(transport=httpx.MockTransport(api)))
    assert [d["mal_tiempo"] for d in p] == [False, True]
    assert p[1]["descripcion"] == "lluvia" and p[0]["max"] == 22.1


def test_tiempos_precalculados_sin_osrm(tmp_path, monkeypatch):
    from navarra_trip.tools import rutas

    def osrm(req):  # 3 puntos, bloques de 2: 4 consultas por modo
        n_src = len(req.url.params["sources"].split(";"))
        n_dst = len(req.url.params["destinations"].split(";"))
        fila = [600.0 if req.url.port == 5000 else 6000.0] * n_dst
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "durations": [fila] * n_src,
                "distances": [[1000.0] * n_dst] * n_src,
            },
        )

    def publico(req):
        assert (
            req.url.host == "routing.openstreetmap.de"
            and "navarra-trip-agent" in req.headers["user-agent"]
        )
        return httpx.Response(503)

    destino = tmp_path / "tiempos.npz"
    rutas.precalcular(
        [PAMPLONA, OLITE, UJUE],
        destino,
        bloque=2,
        client=httpx.Client(transport=httpx.MockTransport(osrm)),
    )
    monkeypatch.setattr(rutas, "TIEMPOS", str(destino))
    assert matriz([PAMPLONA, OLITE]) == [[10.0, 10.0], [10.0, 10.0]]
    assert matriz([PAMPLONA, OLITE], "pie")[0][1] == 100.0
    r = ruta(
        [PAMPLONA, OLITE, PAMPLONA],
        geometria=True,
        client=httpx.Client(transport=httpx.MockTransport(publico)),
    )
    assert r == {
        "km": 2.0,
        "minutos": 20,
        "tramos": [{"km": 1.0, "minutos": 10}] * 2,
        "geometria": None,
    }
    # un punto que no está en la tabla: línea recta corregida, no un error
    lejos = matriz([PAMPLONA, (42.0, -1.0)])[0][1]
    assert 100 < lejos < 200
