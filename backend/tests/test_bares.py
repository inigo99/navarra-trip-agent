from navarra_trip.ingest import bares


def test_fila_de_osm():
    e = {
        "type": "way",
        "id": 7,
        "center": {"lat": 42.8, "lon": -1.6},
        "tags": {"name": "Txoko", "amenity": "bar", "opening_hours": "Mo-Su 12:00-24:00"},
    }
    f = bares.fila(e)
    assert f["id"] == "bar:w7" and f["tipo"] == "bar" and f["horario"] == "Mo-Su 12:00-24:00"
    assert f["url_fuente"] == "https://www.openstreetmap.org/way/7" and (f["lat"], f["lon"]) == (
        42.8,
        -1.6,
    )
