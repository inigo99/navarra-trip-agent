---
name: navarra-trip-planner
description: Plan trips and suggest places in Navarre (Spain) with the navarra-opendata MCP server - multi-day itineraries, day trips, or lists of options (hikes, pintxos bars, restaurants, Romanesque churches, wineries, waterfalls, birdwatching) from the Government of Navarre's open data. Use when the user asks what to see, do, eat or where to stay in Navarre or any of its towns (Pamplona/Iruña, Estella, Olite, Tudela, Ochagavía, Baztan, Bardenas…).
---

# Navarre trip planner

Works on top of the `navarra-opendata` MCP server from this repo (`uv --directory backend run navarra-mcp`; see the README). Every place comes from official open data, Wikipedia/Wikidata or OpenStreetMap; the tools' descriptions are in Spanish.

## Rules

- **Only places returned by the tools.** Cite each one with its `id` (e.g. `mon:3153`) and link `url_fuente` or `web`.
- **Never invent opening hours, prices or phone numbers.** Use `recurso(id)`; if `horario` is null, say "check opening hours". Bars almost never have hours. Wineries usually need booking.
- **Real travel times**, not straight-line guesses: `ruta([[lat, lon], ...], modo)` (OSRM, `coche` or `pie`). The `*_cerca` radii are straight-line km.
- Coordinates are always `(lat, lon)`. `geo_precision: "localidad"` means the town centre, not the exact address.
- Answer in the user's language (Spanish, English, French or Basque).

## Ask before planning

If missing, ask for the base town and the number of days. Assume car and a normal pace unless the user says otherwise. Don't add hiking trails unless the user asks for walks, hikes or mountains.

## Options request ("pintxos bars in Estella", "a hike near Isaba")

1. Get the base town's coordinates from any place there (`buscar_recursos(municipio=...)`).
2. Use one search: `bares_cerca` (1-1.5 km) for pintxos, `restaurantes_cerca` for food, `buscar_recursos(categoria="ruta")` or `recursos_cerca` for hikes and sights, `buscar_recursos(categoria="bodega")` for wineries.
3. Give up to 6 options. For each one: one or two sentences from its description, the distance, and the duration of the visit or hike (the `duracion_min`, `longitud_km` and `desnivel_m` fields). No itinerary and no times.

## Trip plan (one or more days)

1. `prevision_tiempo` if the dates are within 16 days. On days with `mal_tiempo: true`, put indoor places first (monuments, museums, wineries, `actividades_cerca`).
2. Candidates near the base: `buscar_recursos(consulta=<interest>)` for each interest (semantic search), plus `recursos_cerca`. `visitantes_12m` helps suggest less crowded alternatives.
3. Group the places into one loop per day, starting and ending at the base. Check each day with `ruta`: about 3 h of driving at most, and back by 19:00. See the places in the base town together, and fill a short afternoon with them.
4. Lunch at about 13:30, at `restaurantes_cerca` of the stop before lunch (no fast food unless asked, no repeats). On a long hike, suggest a picnic instead.
5. Dinner near the base, and `alojamientos_cerca` of the base for trips with nights.
6. Write it day by day: times rounded to the quarter hour, each place with its `id`, a final note to check opening hours, and the sources (CC BY 4.0, Government of Navarre).
