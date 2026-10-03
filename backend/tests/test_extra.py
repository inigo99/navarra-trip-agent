from datetime import date

from navarra_trip.ingest import extra

FUENTE = {"url_fuente": "u", "licencia": "CC-BY-4.0"}


def test_aves_separa_nombre_zonas_y_texto():
    raw = FUENTE | {
        "registros": [
            {
                "CodRecurso": "3825",
                "Nombre": "Abubilla (Upupa epops)",
                "Caracter": "estival.",
                "Observacion": "fácil.",
                "DescripImpresion": "\nAve con cresta.\nZonas: Zona Media, Pamplona y Ribera.\n\\n\n",
            },
            {
                "CodRecurso": "1",
                "Nombre": "Buitre leonado (Gyps fulvus)",
                "Caracter": "residente.",
                "Observacion": "fácil.",
                "DescripImpresion": "<br>Rapaz.<br />\n<b> </b><br/>\nZonas: todas las zonas<br/>",
            },
        ]
    }
    a, b = extra.aves(raw)
    assert (a["nombre"], a["cientifico"], a["presencia"]) == ("Abubilla", "Upupa epops", "estival")
    assert (
        a["zonas"] == ["Zona Media", "Pamplona", "Ribera"] and a["descripcion"] == "Ave con cresta."
    )
    assert b["zonas"] == ["Todas las zonas"] and b["descripcion"] == "Rapaz."


def test_afluencia_suma_contadores_y_ultimos_12_meses():
    regs = [
        {"MES": f"{y}-{m:02d}-01", "RECURSO": r, "NUM_VISITANTES": "10"}
        for y, m in ((2025, 8), (2025, 9), (2026, 8))
        for r in ("Irati - Arrazola", "Irati - Salazar")
    ]
    regs.append({"MES": "2026-08-01", "RECURSO": "Museo de las brujas", "NUM_VISITANTES": "99"})
    filas = extra.afluencia(
        FUENTE | {"registros": regs},
        {"Irati - Arrazola": "esp:3041", "Irati - Salazar": "esp:3041"},
    )
    assert filas[0] == {"recurso_id": "esp:3041", "mes": date(2025, 8, 1), "visitantes": 20}
    recs = [{"id": "esp:3041"}, {"id": "mon:1"}]
    extra.visitantes_12m(recs, filas)
    assert recs[0]["visitantes_12m"] == 40  # sep-2025 y ago-2026; ago-2025 queda fuera
    assert recs[1]["visitantes_12m"] is None


def test_csv_de_afluencia():
    m = extra.leer_afluencia_manual()
    assert m["Palacio Real de Olite"] == "mon:3153" and "Museo de las brujas" not in m


def test_agroturismos_y_actividades():
    agro = extra.agroturismos_como_alojamientos(
        FUENTE
        | {"registros": [{"COD_REGISTRO": "UAGR0001", "SUBZONA": "Ultzama", "NOMBRE": "Granja"}]}
    )
    assert (
        agro["registros"][0]["COD_INSCRIPCION"] == "UAGR0001"
        and agro["registros"][0]["PLAZAS"] is None
    )
    x = extra.actividades_extra(
        {"MODALIDAD": "m", "TIPO": "Desconocido", "ACTIVIDADES": "Kayak, Rafting"}
    )
    assert x == {"modalidad": "m", "tipo": None, "actividades": ["Kayak", "Rafting"]}


def test_oficinas_desde_geojson():
    raw = FUENTE | {
        "registros": [
            {
                "geometry": {"coordinates": [-1.61, 43.14]},
                "properties": {
                    "RECTUR": "Oficina de Turismo de Bertiz",
                    "ZONATUR": "Pirineos",
                    "DIRECCION": "Centro",
                    "POBLACION": "Oieregi",
                    "TELEFONO": 948592386,
                    "EMAIL": "oit@navarra.es",
                    "URL": "https://x",
                },
            }
        ]
    }
    o = extra.oficinas(raw)[0]
    assert (o["id"], o["telefono"], o["lon"], o["lat"]) == ("ofi:1", "948592386", -1.61, 43.14)


def test_zona_y_campos_manuales():
    rs = [
        {"id": "a", "zona": "Pirineo", "lon": -1.1, "lat": 42.9},
        {"id": "b", "zona": "Ribera", "lon": -1.6, "lat": 42.1},
        {"id": "c", "zona": None, "lon": -1.12, "lat": 42.95, "horario": None, "de_pago": None},
    ]
    assert extra.completar_zonas(rs) == 1 and rs[2]["zona"] == "Pirineo"
    extra.aplicar_manual(rs, {"c": {"horario": "Tu-Su 10:00-14:00", "de_pago": "sí"}})
    assert rs[2]["horario"] == "Tu-Su 10:00-14:00" and rs[2]["de_pago"] is True


def test_duracion_por_subcategoria_y_manual():
    iglesia = {"id": "m", "categoria": "monumento", "subcategorias": ["Iglesias y ermitas"]}
    museo = {"id": "c", "categoria": "monumento", "subcategorias": ["Castillos/Palacios", "Plazas"]}
    raro = {"id": "x", "categoria": "natural", "subcategorias": ["Otros espacios"]}
    ruta = {"id": "ruta:a", "categoria": "ruta", "subcategorias": [], "duracion_min": 195}
    rs = [iglesia, museo, raro, ruta]
    extra.aplicar_manual(rs, {"m": {"duracion": "50"}})
    assert [r["duracion_min"] for r in rs] == [50, 60, 60, 195]
