import httpx

from navarra_trip.ingest.osm import completar, ficha
from navarra_trip.ingest.wikidata import Cliente

URDAX = {
    "type": "node",
    "id": 605052713,
    "lat": 43.27674,
    "lon": -1.51234,
    "tags": {
        "name": "Urdazubiko lezeak",
        "name:es": "Cuevas de Urdax",
        "name:eu": "Urdazubiko lezeak",
        "fee": "yes",
        "website": "https://cuevasurdax.com/",
        "opening_hours": "Tu-Su 11:00-18:00",
        "tourism": "attraction",
    },
}
OTRO = {
    "type": "way",
    "id": 1,
    "center": {"lat": 43.2665, "lon": -1.503},
    "tags": {"name": "Urdazubiko turismo bulegoa", "tourism": "information"},
}


def _r(**kw):
    return {
        "id": "esp:3037",
        "nombre": "Cuevas de Urdazubi/Urdax",
        "municipio": "Urdazubi/Urdax",
        "categoria": "natural",
        "subcategorias": ["Cuevas"],
        "estilo": None,
        "zona": "Pirineos",
        "lon": -1.51212,
        "lat": 43.27682,
        "descripcion": None,
        "descripcion_fuente": None,
        "url_descripcion": None,
        "url_fuente": "https://datosabiertos.navarra.es/x",
    } | kw


def _cli(elementos):
    api = httpx.MockTransport(lambda r: httpx.Response(200, json={"elements": elementos}))
    return Cliente(httpx.Client(transport=api), {}, pausa=0)


def test_enlaza_con_osm_y_hace_ficha():
    r = _r()
    completar([r], _cli([URDAX, OTRO]))
    assert r["osm_id"] == "node/605052713"
    assert r["horario"] == "Tu-Su 11:00-18:00" and r["de_pago"] is True
    assert r["web"] == "https://cuevasurdax.com/"
    assert r["descripcion_fuente"] == "ficha"
    assert r["descripcion"] == (
        "Cuevas de Urdazubi/Urdax. Tipo: Cuevas. Municipio: Urdazubi/Urdax. Zona: Pirineos. "
        "En euskera: Urdazubiko lezeak."
    )
    assert r["url_descripcion"] == "https://www.openstreetmap.org/node/605052713"


def test_no_pisa_la_descripcion_de_wikipedia_ni_inventa_sin_coincidencia():
    r = _r(descripcion="Texto de Wikipedia", descripcion_fuente="wikipedia")
    completar([r], _cli([OTRO]))
    assert r["osm_id"] is None and r["horario"] is None
    assert r["descripcion"] == "Texto de Wikipedia"


def test_sin_coordenadas_solo_ficha():
    r = _r(lon=None, lat=None)
    completar([r], Cliente(None, {}))  # no debe hacer peticiones
    assert r["descripcion"].startswith("Cuevas de Urdazubi/Urdax. Tipo: Cuevas.")
    assert r["url_descripcion"] == r["url_fuente"]


def test_ficha_incluye_estilo():
    assert "Estilo: Románico." in ficha(_r(subcategorias=["Iglesias y ermitas"], estilo="Románico"))


def test_overpass_caido_no_para_ni_se_cachea():
    cache = {}
    api = httpx.MockTransport(lambda r: httpx.Response(504))
    r = _r()
    completar([r], Cliente(httpx.Client(transport=api), cache, pausa=0))
    assert r["osm_id"] is None and r["descripcion_fuente"] == "ficha"
    assert cache == {}


def test_si_la_principal_falla_usa_otra_instancia_y_luego_la_cache():
    def api(req):
        if req.url.host == "overpass-api.de":
            return httpx.Response(504)
        return httpx.Response(200, json={"elements": [URDAX]})

    cache = {}
    r = _r()
    completar([r], Cliente(httpx.Client(transport=httpx.MockTransport(api)), cache, pausa=0))
    assert r["osm_id"] == "node/605052713"
    r2 = _r()
    completar([r2], Cliente(None, cache))  # sin red: sale de la caché de la otra instancia
    assert r2["osm_id"] == "node/605052713"


def test_descripcion_corta_de_wikidata_se_completa_con_la_ficha():
    r = _r(
        lon=None, lat=None, descripcion="bien de interés cultural", descripcion_fuente="wikidata"
    )
    completar([r], Cliente(None, {}))
    assert r["descripcion"].startswith("Cuevas de Urdazubi/Urdax. Tipo: Cuevas.")
    assert r["descripcion"].endswith("Bien de interés cultural.")


def test_descripcion_manual_no_se_toca():
    r = _r(lon=None, lat=None, descripcion="Corta.", descripcion_fuente="manual")
    completar([r], Cliente(None, {}))
    assert r["descripcion"] == "Corta."
