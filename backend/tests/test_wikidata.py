import httpx

from navarra_trip.ingest.wikidata import Cliente, emparejar, enriquecer, leer_manual, similitud


def test_similitud_ignora_el_municipio_y_las_tildes():
    assert similitud("La Ciudadela", "Pamplona", "Ciudadela de Pamplona") == 1
    assert similitud("Foz de Lumbier", "Lumbier", "foz de Lumbier") == 1
    assert similitud("Iglesia de San Miguel", "Corella", "plaza de toros de Corella") < 0.5


def _r(**kw):
    return {
        "id": "mon:1",
        "nombre": "Castillo de Javier",
        "municipio": "Javier",
        "categoria": "monumento",
        "lon": -1.2,
        "lat": 42.6,
        "url_fuente": "https://datosabiertos.navarra.es/x",
    } | kw


def test_emparejar_acepta_solo_si_es_claro():
    claro = [
        {"q": "Q1", "textos": ["Castillo de Javier"], "km": 0.0},
        {"q": "Q2", "textos": ["casa consistorial de Javier"], "km": 0.3},
    ]
    assert emparejar(_r(), claro)[0] == "Q1"
    empate = [
        {"q": "Q1", "textos": ["Castillo de Javier"], "km": 0.0},
        {"q": "Q2", "textos": ["castillo Javier"], "km": 0.0},
    ]
    q, ranking = emparejar(_r(), empate)
    assert q is None and len(ranking) == 2


def test_alias_cuenta_como_nombre():
    cands = [
        {"q": "Q1934670", "textos": ["Royal Palace of Olite", "Palacio Real de Olite"], "km": 0.1}
    ]
    assert emparejar(_r(nombre="Palacio Real de Olite", municipio="Olite"), cands)[0] == "Q1934670"


def _api(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    if request.url.host == "query.wikidata.org":
        fila = {
            "item": {"value": "http://www.wikidata.org/entity/Q112738"},
            "label": {"value": "Castillo de Javier"},
            "alias": {"value": ""},
            "dist": {"value": "0.02"},
        }
        return httpx.Response(200, json={"results": {"bindings": [fila]}})
    if request.url.host == "www.wikidata.org":
        ents = {
            "Q112738": {
                "descriptions": {"es": {"value": "castillo en Navarra"}},
                "sitelinks": {"eswiki": {"title": "Castillo de Javier"}},
                "claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Javier castillo.jpg"}}}]},
            },
            "Q63301905": {
                "descriptions": {"es": {"value": "Cascada de Navarra"}},
                "claims": {
                    "P625": [
                        {
                            "mainsnak": {
                                "datavalue": {"value": {"latitude": 42.76, "longitude": -2.11}}
                            }
                        }
                    ]
                },
            },
        }
        return httpx.Response(200, json={"entities": {q: ents[q] for q in p["ids"].split("|")}})
    assert p["exintro"] == "1"
    return httpx.Response(
        200,
        json={
            "query": {
                "pages": {
                    "1": {"title": "Castillo de Javier", "extract": "El castillo de Javier es..."}
                }
            }
        },
    )


def _recursos():
    return [
        _r(),
        _r(id="esp:4310", nombre="Nacedero del Urederra", categoria="natural", lon=None, lat=None),
        _r(id="esp:5871", nombre="Cascada del Cubo", categoria="natural", lon=None, lat=None),
    ]


def _m(q=None, lon=None, lat=None, d=None):
    return {"q": q, "lon": lon, "lat": lat, "descripcion": d}


def test_enriquecer_de_principio_a_fin():
    recursos = _recursos()
    manual = {"esp:4310": _m("Q63301905"), "esp:5871": _m("-", -1.10, 43.0, "Cascada de 20 m.")}
    cache = {}
    revision = enriquecer(
        recursos, Cliente(httpx.Client(transport=httpx.MockTransport(_api)), cache, pausa=0), manual
    )
    javier, nacedero, cubo = recursos
    assert javier["wikidata_id"] == "Q112738"
    assert javier["descripcion"] == "El castillo de Javier es..."
    assert javier["url_descripcion"] == "https://es.wikipedia.org/wiki/Castillo_de_Javier"
    assert javier["imagen_url"].endswith("Javier_castillo.jpg")
    assert (nacedero["lon"], nacedero["lat"]) == (-2.11, 42.76)  # coordenadas de Wikidata
    assert nacedero["descripcion"] == "Cascada de Navarra"  # sin Wikipedia: descripción corta
    assert cubo["wikidata_id"] is None
    assert (cubo["descripcion"], cubo["descripcion_fuente"]) == ("Cascada de 20 m.", "manual")
    assert (cubo["lon"], cubo["lat"]) == (-1.10, 43.0)  # coordenadas de la revisión manual
    assert revision == []
    # 2.ª vez sin red: todo sale de la caché
    enriquecer(_recursos(), Cliente(None, cache), manual)


def test_manual_sin_q_deja_el_emparejado_automatico():
    # una fila solo con horario no debe borrar el enlace a Wikidata
    recursos = _recursos()[:1]
    cli = Cliente(httpx.Client(transport=httpx.MockTransport(_api)), {}, pausa=0)
    enriquecer(recursos, cli, {"mon:1": _m(None)})
    assert recursos[0]["wikidata_id"] == "Q112738"


def test_manual_csv_es_coherente():
    manual = leer_manual()
    assert len(manual) > 50
    assert all(m["q"] in (None, "-") or m["q"].startswith("Q") for m in manual.values())
    assert manual["mon:3153"]["q"] == "Q1934670"
    assert manual["mon:5414"]["descripcion"].startswith("El Castillo de Cortes")
    lon, lat = manual["esp:5871"]["lon"], manual["esp:5871"]["lat"]
    assert -2.6 < lon < -0.7 and 41.9 < lat < 43.4  # orden lon, lat
