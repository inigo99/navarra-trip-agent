import json

from fastapi.testclient import TestClient
from test_planner import _grafo, con  # noqa: F401  (con es un fixture)

from navarra_trip import api
from navarra_trip.planner import Requisitos


def _eventos(r):
    return [json.loads(x.removeprefix("data: ")) for x in r.text.split("\n\n") if x.strip()]


def test_plan_por_sse_guardado_y_gpx(con, monkeypatch, tmp_path):  # noqa: F811
    req = Requisitos(dias=2, base="Olite", intereses=["románica"])
    monkeypatch.setattr(api, "_grafo", lambda: _grafo(con, req, ["Bonito."] * 2))
    monkeypatch.setattr(api, "PLANES", tmp_path)
    cli = TestClient(api.app)
    ev = _eventos(cli.post("/plan", json={"peticion": "2 días en Olite"}))
    pasos = [e["paso"] for e in ev if "paso" in e]
    assert pasos[:2] == ["interpretar", "candidatos"] and "redactar" in pasos
    fin = ev[-1]
    assert "candidatos" not in fin["plan"] and fin["plan"]["frases"] and "Bonito." in fin["texto"]
    assert cli.get(f"/plan/{fin['id']}").json()["texto"] == fin["texto"]
    gpx = cli.get(f"/plan/{fin['id']}/gpx")
    assert gpx.headers["content-type"].startswith("application/gpx+xml")
    assert gpx.text.count("<wpt") == sum(
        len(d["paradas"]) + bool(d["comida"]) + bool(d.get("cena")) for d in fin["plan"]["dias"]
    )
    assert cli.get("/plan/../../x").status_code == 404
    assert cli.get("/plan/abcdefgh").status_code == 404


def test_plan_que_pregunta(con, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(api, "_grafo", lambda: _grafo(con, Requisitos(base="Olite"), []))
    ev = _eventos(TestClient(api.app).post("/plan", json={"peticion": "Escapada a Olite"}))
    assert ev[-1] == {"pregunta": "¿Cuántos días dura el viaje?"}


def test_ajustar_quita_lugares_y_guarda_otro_plan(con, monkeypatch, tmp_path):  # noqa: F811
    req = Requisitos(dias=1, base="Olite", intereses=["románica"])
    monkeypatch.setattr(api, "_grafo", lambda: _grafo(con, req, ["Bonito."] * 2))
    monkeypatch.setattr(api, "PLANES", tmp_path)
    cli = TestClient(api.app)
    orig = _eventos(cli.post("/plan", json={"peticion": "Un día en Olite."}))[-1]
    quitar = orig["plan"]["dias"][0]["paradas"][0]["id"]
    nuevo = _eventos(
        cli.post(f"/plan/{orig['id']}/ajustar", json={"cambio": "sin prisa", "quitar": [quitar]})
    )[-1]
    assert nuevo["origen"] == orig["id"] and nuevo["peticion"] == "Un día en Olite. sin prisa"
    assert quitar not in {p["id"] for d in nuevo["plan"]["dias"] for p in d["paradas"]}
    assert nuevo["plan"]["excluidos"] == [quitar]
    assert cli.post(f"/plan/{orig['id']}/ajustar", json={}).status_code == 422


def test_quitar_sin_cambio_deja_igual_los_otros_dias(con, monkeypatch, tmp_path):  # noqa: F811
    req = Requisitos(dias=2, base="Olite", intereses=["románica"])
    monkeypatch.setattr(api, "_grafo", lambda: _grafo(con, req, ["Bonito."] * 4))
    monkeypatch.setattr(api, "PLANES", tmp_path)
    cli = TestClient(api.app)
    orig = _eventos(cli.post("/plan", json={"peticion": "Dos días en Olite."}))[-1]
    dias = [[p["id"] for p in d["paradas"]] for d in orig["plan"]["dias"]]
    quitar = dias[0][0]
    nuevo = _eventos(cli.post(f"/plan/{orig['id']}/ajustar", json={"quitar": [quitar]}))[-1]
    nuevos = [[p["id"] for p in d["paradas"]] for d in nuevo["plan"]["dias"]]
    assert sorted(nuevos[1]) == sorted(dias[1]) and quitar not in nuevos[0]


def test_demo_exporta_planes_fichas_e_indice(con, tmp_path):  # noqa: F811
    from navarra_trip import demo

    req = Requisitos(dias=1, base="Olite", intereses=["románica"])
    indice = demo.exportar(
        _grafo(con, req, ["Bonito."] * 2), con, {"olite": "Un día en Olite"}, tmp_path
    )
    assert indice == [{"id": "olite", "peticion": "Un día en Olite"}]
    doc = json.loads((tmp_path / "olite.json").read_text(encoding="utf-8"))
    fichas = json.loads((tmp_path / "recursos.json").read_text(encoding="utf-8"))
    paradas = {p["id"] for d in doc["plan"]["dias"] for p in d["paradas"]}
    assert paradas <= set(fichas) and (tmp_path / "olite.gpx").read_bytes().startswith(b"<?xml")


def test_gpx_de_opciones():
    doc = {
        "peticion": "Dime bares en Olite",
        "plan": {
            "dias": [],
            "opciones": [
                {
                    "id": "bar:n1",
                    "nombre": "Bar 1",
                    "lat": 42.48,
                    "lon": -1.65,
                    "localidad": "Olite",
                }
            ],
        },
    }
    assert api.a_gpx(doc).decode().count("<wpt") == 1


def test_limite_por_ip(con, monkeypatch, tmp_path):  # noqa: F811
    req = Requisitos(dias=1, base="Olite", intereses=["románica"])
    monkeypatch.setattr(api, "_grafo", lambda: _grafo(con, req, ["Bonito."] * 2))
    monkeypatch.setattr(api, "PLANES", tmp_path)
    monkeypatch.setattr(api, "LIMITE_HORA", 1)
    monkeypatch.setattr(api, "_peticiones", api.defaultdict(api.deque))
    cli = TestClient(api.app)
    ip = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}
    assert cli.post("/plan", json={"peticion": "Un día en Olite"}, headers=ip).status_code == 200
    assert cli.post("/plan", json={"peticion": "Un día en Olite"}, headers=ip).status_code == 429
    otra = {"x-forwarded-for": "5.6.7.8"}
    assert cli.post("/plan", json={"peticion": "Un día en Olite"}, headers=otra).status_code == 200


def test_demo_regenerar_uno_conserva_los_demas(con, tmp_path):  # noqa: F811
    from navarra_trip import demo

    req = Requisitos(dias=1, base="Olite", intereses=["románica"])
    g = lambda: _grafo(con, req, ["Bonito."] * 4)  # noqa: E731
    demo.exportar(g(), con, {"a": "Un día en Olite", "b": "Otro día en Olite"}, tmp_path)
    demo.exportar(g(), con, {"a": "Un día en Olite, otra vez"}, tmp_path)
    indice = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert {e["id"]: e["peticion"] for e in indice} == {
        "a": "Un día en Olite, otra vez",
        "b": "Otro día en Olite",
    }
