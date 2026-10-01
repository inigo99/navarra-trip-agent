import duckdb

from navarra_trip.ingest.mapa import geojson


def test_geojson_incluye_las_tres_tablas_y_omite_recursos_sin_coordenadas():
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE recurso AS SELECT * FROM (VALUES ('mon:1','A','monumento',-1.6::DOUBLE,42.8::DOUBLE), ('esp:2','B','natural',NULL,NULL)) t(id,nombre,categoria,lon,lat)"
    )
    con.execute(
        "CREATE TABLE alojamiento AS SELECT 'aloj:1' id,'H' nombre,'Hotel' modalidad,'direccion' geo_precision,-1.7::DOUBLE lon,42.9::DOUBLE lat"
    )
    con.execute(
        "CREATE TABLE restaurante AS SELECT 'rest:1' id,'R' nombre,NULL especialidad,'localidad' geo_precision,-1.5::DOUBLE lon,42.1::DOUBLE lat"
    )
    fc = geojson(con)
    assert [f["properties"]["id"] for f in fc["features"]] == ["mon:1", "aloj:1", "rest:1"]
    assert fc["features"][0]["geometry"]["coordinates"] == [-1.6, 42.8]
