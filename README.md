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
uv run navarra-mapa               # data/mapa.html: mapa de control con todos los puntos
uv sync --extra semantica && uv run navarra-indexar   # índice semántico en data/lancedb
```

## Servidor MCP (`navarra-opendata-mcp`)

Herramientas: `buscar_recursos`, `recursos_cerca`, `alojamientos_cerca`, `restaurantes_cerca`,
`recurso`, `actividades_cerca`, `oficinas_turismo`, `aves`, `ruta`, `prevision_tiempo` y `conjuntos`. En Claude Desktop
(`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "navarra-opendata": {
      "command": "uv",
      "args": ["--directory", "C:\\ruta\\navarra-trip-agent\\backend", "run", "--extra", "semantica", "navarra-mcp"]
    }
  }
}
```

## Rutas (OSRM)

```powershell
mkdir infra\osrm
Invoke-WebRequest https://download.geofabrik.de/europe/spain/navarra-latest.osm.pbf -OutFile infra\osrm\navarra-latest.osm.pbf
docker compose -f infra/docker-compose.yml up -d
curl.exe "http://localhost:5000/route/v1/driving/-1.6432,42.8125;-1.6498,42.4797?overview=false"   # Pamplona -> Olite
```

Coche en `:5000`, a pie en `:5001`.

## Datos

Fuente: [datosabiertos.navarra.es](https://datosabiertos.navarra.es), licencia CC BY 4.0: arte y monumentos, espacios naturales,
alojamientos y agroturismos, restaurantes y empresas de actividades del Registro de Turismo, turismo ornitológico,
afluencia a recursos turísticos y oficinas de turismo (IDENA).
Geolocalización de alojamientos y restaurantes: [CartoCiudad](https://www.cartociudad.es) (IGN), CC BY 4.0.
Rutas y mapa: © colaboradores de [OpenStreetMap](https://www.openstreetmap.org/copyright), ODbL.
