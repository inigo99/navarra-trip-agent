import httpx

from navarra_trip.ingest.geocode import Geocoder, parecida, portal

# Respuestas reales de CartoCiudad (recortadas)
FIND = {
    "Acella 5, Pamplona": {
        "type": "portal",
        "address": "ACELLA / AZELLA",
        "provinceCode": "31",
        "lat": 42.806,
        "lng": -1.663,
    },
    "Los Fueros 1, Bera": {
        "type": "portal",
        "address": "AFUERAS",
        "provinceCode": "22",
        "lat": 42.35,
        "lng": 0.57,
    },
}
CANDIDATOS = {
    "Bera": [{"id": "p1", "type": "poblacion", "provinceCode": "31", "muni": "Bera"}],
    "Arce": [
        {"id": "p2", "type": "poblacion", "provinceCode": "31", "muni": "Urraul Alto"},
        {"id": "p3", "type": "poblacion", "provinceCode": "31", "muni": "Arce/Artzi"},
    ],
    "Baztan": [{"id": "31050", "type": "Municipio", "provinceCode": "31", "muni": "Baztan"}],
}
POR_ID = {"p1": (43.28, -1.68), "p2": (42.9, -1.2), "p3": (42.8, -1.4), "31050": (43.159, -1.506)}


def _api(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    if request.url.path.endswith("candidates"):
        assert p["provincia_filter"] == "Navarra"
        return httpx.Response(200, json=CANDIDATOS.get(p["q"], []))
    if "id" in p:
        lat, lng = POR_ID[p["id"]]
        return httpx.Response(200, json={"lat": lat, "lng": lng})
    if p["q"] == "de Urbasa 31, Olazti":
        return httpx.Response(500)
    j = FIND.get(p["q"])
    return httpx.Response(200, json=j) if j else httpx.Response(204)


def _geo(api=_api):
    return Geocoder(httpx.Client(transport=httpx.MockTransport(api)), {}, pausa=0)


def test_portal_y_calle_parecida():
    assert portal("Acella 5 3º D") == ("Acella", 5)
    assert portal("Mayor 0 S/N") is None
    assert portal("Barrio Iriberri") is None
    assert parecida("Guipuzcoa", "GIPUZKOA")
    assert not parecida("Calle Inventada", "DE LA CUENCA DE PAMPLONA / IRUÑERRIA")


def test_portal_exacto_sin_piso():
    assert _geo().geocodificar("Acella 5 3º D", "Pamplona / Iruña", "Pamplona / Iruña") == (
        -1.663,
        42.806,
        "direccion",
    )


def test_rechaza_otra_provincia_y_cae_a_localidad():
    assert _geo().geocodificar("Los Fueros 1", "Bera", "Bera") == (-1.68, 43.28, "localidad")


def test_localidad_homonima_prefiere_su_municipio():
    assert _geo().geocodificar("Mayor 0", "Arce", "Arce / Artzi") == (-1.4, 42.8, "localidad")


def test_sin_portal_ni_localidad_cae_a_municipio():
    assert _geo().geocodificar("Mayor 0 S/N", "Inventada", "Baztan") == (
        -1.506,
        43.159,
        "municipio",
    )


def test_cache_evita_repetir_peticiones():
    llamadas = []
    g = _geo(lambda r: llamadas.append(r) or _api(r))
    g.geocodificar("Los Fueros 1", "Bera", "Bera")
    n = len(llamadas)
    g.geocodificar("Los Fueros 1", "Bera", "Bera")
    assert len(llamadas) == n


def test_error_500_persistente_cuenta_como_sin_resultado():
    llamadas = []
    g = _geo(lambda r: llamadas.append(r) or _api(r))
    assert g.buscar("de Urbasa 31, Olazti") is None
    assert len(llamadas) == 3
    assert "de Urbasa 31, Olazti" in g.cache


def test_error_500_puntual_se_reintenta():
    respuestas = iter([httpx.Response(500), httpx.Response(200, json=FIND["Acella 5, Pamplona"])])
    g = _geo(lambda r: next(respuestas))
    assert g.buscar("Acella 5, Pamplona")["address"] == "ACELLA / AZELLA"
