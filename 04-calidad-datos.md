# navarra-trip-agent: calidad de los datos y esquema (S1, sesión 3)

> Perfilado del 01-10-2026 sobre la descarga de `navarra-descargar`. Licencia de los 4 conjuntos: CC BY 4.0.

## 1. Resumen por conjunto

| Conjunto | Filas | Únicas | Coordenadas | Actualización |
|---|---|---|---|---|
| Arte y monumentos | 95 | 95 | UTM 30N (EPSG:25830) en `GEORR_X/Y`; las 95 caen dentro de Navarra | Contenido de abril de 2021 (recurso tocado en 2023) |
| Espacios naturales | 66 | **63** | UTM; **2 con (0, 0)**: Cascada del Cubo y Nacedero del Urederra | Abril de 2021 |
| Alojamientos | 2 844 | **2 773** | **Ninguna** | Diaria |
| Restaurantes | 554 | **540** | **Ninguna** | Diaria |

## 2. Problemas y cómo se tratan

| Problema | Gravedad | Tratamiento en la normalización |
|---|---|---|
| Filas idénticas repetidas (70 en alojamientos y 14 en restaurantes; p. ej. `UAT01592` aparece 8 veces) | Media | `drop_duplicates()` y después una fila por `COD_INSCRIPCION`. Queda 1 id de alojamiento con filas distintas: se conserva la primera |
| Espacios con el mismo `Codrecurso` y distinto `TIPO` (Urbasa-Andía, Bertiz, Aralar) | Baja | Un único recurso con **varias subcategorías** |
| 2 espacios con coordenadas (0, 0) | **Alta** (son lugares de primera) | `geom = NULL`; en S2 se rellenan con las coordenadas de Wikidata. Nunca se inventan |
| Nombres de monumentos repetidos en lugares distintos (5 pares, p. ej. "Catedral de Santa María" en Pamplona y en Tudela) | Media para el LLM | El id es único; al mostrar el nombre se añade **"nombre (municipio)"** |
| Sin descripción en monumentos ni espacios | Alta para la búsqueda semántica | Wikipedia/Wikidata en S2 (ya decidido) |
| Direcciones con `0` o `S/N` (375 alojamientos y 188 restaurantes, sobre todo rurales) | Media | CartoCiudad; si falla, centroide de la **localidad** (443 distintas, más fina que el municipio; p. ej. Baztan agrupa a Elizondo, Arizkun…) y si no, del municipio. `geo_precision` = `direccion` / `localidad` / `municipio` |
| CP genérico `31000` (49 restaurantes de Pamplona) | Baja | El geocodificador se apoya en dirección + localidad, no en el CP |
| Email, web y teléfono: "No se consiente la publicación" en ≥ 95 % | — | **No se cargan** en la v1. Se enlaza a la ficha del conjunto |
| `Especialidad = "Desconocido"` (68 restaurantes) | Baja | `NULL` |
| `ImgFichero` sin URL base conocida | Baja | No se usa. Las imágenes salen de Wikidata (S2) |
| Columnas vacías o constantes (`Distancia`, `CodCategoria`, `DIPLOMACOMPROMISO`, `URLNombreBuscador`) | — | Se descartan |

## 3. Categorías

- **Monumentos (`Tipo`, 17 valores):** Iglesias y ermitas 38, Castillos/Palacios 16, Restos arqueológicos 8, Monasterios 7… `ESTILO` (21 valores, 18 % nulos) sirve para el interés "románico" (32 con Románico).
- **Espacios (`TIPO`, 12 valores):** Miradores 16, Valles 10, Parques y jardines 7, Bosques 6…
- **Alojamientos (`MODALIDAD`, 15 valores)** se agrupan en `tipo`:
  - `apartamento`: Apartamento Turístico, Apartamento, Bloque apartamentos, Hotel-apartamento, Vivienda Turística.
  - `rural`: Casa rural vivienda/habitaciones, Apartamento Turístico Rural, Vivienda Turística Rural, Hotel rural.
  - `hotel`: Hotel, Hostal, Pensión.
  - `albergue`: Albergue turístico.
  - `camping`: Camping.
- **Zonas:** monumentos y espacios usan `DescripZona` (4 zonas); el Registro de Turismo usa `SUB_ZONA` (8 subzonas). No se cruzan: lo espacial manda.

## 4. Esquema definitivo (DuckDB, WGS84)

> Implementado en la sesión 4 (`ingest/normaliza.py`). Las coordenadas se guardan como `lon` y `lat` (DOUBLE, NULL si no hay). La extensión spatial de DuckDB se carga **al consultar** (S2: `ST_Point(lon, lat)`), no en la ingesta: así la CI no depende de descargar extensiones. La conversión UTM → WGS84 se hace con pyproj, y la comprobación de que cada punto cae dentro de Navarra, con shapely y el polígono `spatial` del propio conjunto CKAN.

```text
recurso
  id            TEXT PK   -- 'mon:2859' | 'esp:3058'
  nombre        TEXT
  categoria     TEXT      -- 'monumento' | 'natural'
  subcategorias TEXT[]    -- Tipo/TIPO (varias en espacios)
  estilo        TEXT?     -- solo monumentos
  municipio     TEXT      -- NombreLocalidad
  zona          TEXT?     -- DescripZona
  lon, lat      DOUBLE?   -- NULL si venía (0,0)
  url_fuente    TEXT      -- ficha del conjunto en datosabiertos.navarra.es
  licencia      TEXT      -- 'CC-BY-4.0'

alojamiento
  id TEXT PK ('aloj:UH000003') · nombre · modalidad · tipo · categoria · plazas INT
  direccion · localidad · municipio · subzona
  lon, lat DOUBLE · geo_precision ('direccion'|'localidad'|'municipio')
  url_fuente · licencia

restaurante
  id TEXT PK ('rest:UR000924') · nombre · categoria · especialidad?
  direccion · localidad · municipio · subzona
  lon, lat DOUBLE · geo_precision · url_fuente · licencia
```

Cambios respecto a `03-arquitectura.md`:

- `subcategoria` pasa a ser `subcategorias[]`.
- Se añaden `estilo`, `tipo`, `geo_precision` y `localidad`.
- `descripcion`, `wikidata_id`, `afluencia_anual` y `horario` llegan en S2/v1.1.

## 5. Tests de la normalización (sesión 4)

- Ids únicos en las 3 tablas.
- Todo `geom` no nulo cae dentro del contorno de Navarra (el `spatial` del propio conjunto CKAN sirve como polígono).
- `licencia` y `url_fuente` nunca nulos.
- Recuentos esperados: 95 monumentos, 63 espacios, alrededor de 2 773 alojamientos y alrededor de 540 restaurantes.

## 6. Geocodificación con CartoCiudad (formatos probados a mano)

- Dirección: `"{calle} {número}, {localidad}"`, **sin piso ni letra y sin ", Navarra"**; con ellos devuelve 204. En una muestra de 12, el formato largo encontró 2 y el corto 7.
- Solo se acepta `type = portal`, `provinceCode = 31` y una calle parecida (difflib ≥ 0,6, sin tildes). Así se descartan casos como "Los Fueros 1, Bera", que devolvía una calle de Huesca.
- Si no, el centroide de la localidad y, en último caso, el del municipio. `find` **no filtra por provincia** ("Los Arcos" cae en Burgos y "Sada" en A Coruña), así que se usa `candidates?q=…&provincia_filter=Navarra`, se prefiere el candidato de su municipio (hay localidades homónimas) y después `find?id=…&type=…` para obtener las coordenadas.
- CartoCiudad devuelve 500 siempre con algunas consultas: se reintenta 3 veces y luego se toma como "sin resultado".
- Comprobación "fuera de Navarra": el contorno del CKAN está simplificado, así que se le da un **margen de unos 5 km** (`MARGEN_GRADOS`). Sin él salían 8 falsos positivos en la frontera (la Mesa de los Tres Reyes, las ventas de Dantxarinea, el embalse de Yesa…). Es solo un control grueso: lo que de verdad filtra es `provinceCode = 31`.
- Resultado de la 1.ª pasada (01-10-2026): alojamientos 1 776 con dirección exacta, 940 con localidad, 51 con municipio y 6 sin coordenadas; restaurantes 284 / 220 / 33 / 3. Los 9 sin coordenadas eran de Imárcoain, Los Arcos, Sada y Zabalegui por el problema de la provincia, ya corregido.
- Caché de consultas en `data/geocache.json` (no se versiona).
