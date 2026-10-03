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
