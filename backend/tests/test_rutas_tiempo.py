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
