# navarra-trip-agent

Agente que diseña escapadas de varios días por Navarra con los datos abiertos del Gobierno de Navarra:
plan por días con tiempos reales, mapa, alojamientos y restaurantes del Registro de Turismo y la fuente de cada lugar.

> Estado: semana 1 (datos). En construcción.

## Estructura

- `backend/`: Python 3.12 + uv (ingesta, herramientas, agente, servidor MCP y API).
- `web/`: Next.js + TypeScript (llega en la semana 5).

## Desarrollo

```bash
cd backend
uv sync
uv run pytest
uv run navarra-descargar          # 4 conjuntos de la v1 a data/raw/ (con caché)
uv run navarra-descargar --force  # volver a descargar
uv run navarra-normalizar         # data/navarra.duckdb (geolocaliza con CartoCiudad, con caché)
```

## Datos

Fuente: [datosabiertos.navarra.es](https://datosabiertos.navarra.es), licencia CC BY 4.0.
Geolocalización de alojamientos y restaurantes: [CartoCiudad](https://www.cartociudad.es) (IGN), CC BY 4.0.
