# navarra-trip-agent

An agent that plans multi-day trips around Navarre (Spain) using the Government of Navarre's open data:
a day-by-day plan with real travel times, a map, lodging and restaurants from the official tourism registry, and the source of every place.

🇪🇸 *¿Buscas la versión en español? Está en [`README.es.md`](README.es.md).*

**Demo: [navarra-trip-agent-pearl.vercel.app](https://navarra-trip-agent-pearl.vercel.app)** (sample plans; to request your own, run it locally).

## Layout

- `backend/`: Python 3.12 + uv (ingestion, tools, agent, MCP server and API).
- `web/`: Next.js 16 + TypeScript + Tailwind + MapLibre.

## Development

```bash
cd backend
uv sync
uv run pytest
uv run navarra-descargar          # the 4 v1 datasets into data/raw/ (cached)
uv run navarra-descargar --force  # download again
uv run navarra-normalizar         # data/navarra.duckdb (geocodes with CartoCiudad, cached)
uv run navarra-mapa               # data/mapa.html: control map with every point
uv sync --extra semantica && uv run navarra-indexar   # semantic index in data/lancedb
```

## MCP server (`navarra-opendata-mcp`)

Tools: `buscar_recursos`, `recursos_cerca`, `alojamientos_cerca`, `restaurantes_cerca`,
`bares_cerca`, `recurso`, `actividades_cerca`, `oficinas_turismo`, `aves`, `ruta`, `prevision_tiempo` and `conjuntos`. In Claude Desktop
(`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "navarra-opendata": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\navarra-trip-agent\\backend", "run", "--extra", "semantica", "navarra-mcp"]
    }
  }
}
```

## Planning agent

Built with LangGraph. The LLM only interprets the request and writes one or two sentences per place.
Everything else is deterministic code (`planner.py`), and the final text is assembled by code:

- **Candidates** around the base town (30 km by car, 4 km on foot), taking turns between the ranking
  of each interest (semantic search with multilingual-e5-base). Hiking trails only if asked for.
- **Days** built with real OSRM travel times: each interest takes a turn opening a day, and the place
  that adds the least to the return trip is added next, until the end time set by the pace (17:30, 19:00 or 20:00).
  Places in the base town are visited together, on the same day. Walks and hours on foot are capped; when it rains, monuments are picked.
- **Order** is exactly optimal (Held-Karp), with times rounded to the quarter hour, lunch (or a picnic on the way),
  longer visits if the afternoon has room, dinner or a pintxos round, and lodging (if there is an overnight stay).
- **Options without an itinerary** ("a mountain walk near Isaba", "pintxos bars in Estella"):
  up to 6 places, trails, bars or restaurants with their duration and distance.
- **Interpretation** with safeguards in code: language, relative dates ("on Saturday", "in two months"),
  transport and pace only if the request states them, and town names with aliases and typos.

```powershell
ollama pull qwen2.5:7b
uv run --extra semantica navarra-plan "3 días en Estella, me gusta el románico y la naturaleza"
$env:NAVARRA_LLM = "groq:openai/gpt-oss-120b"   # optional, with GROQ_API_KEY (free)
```

The full plan (with GeoJSON) is written to `data/plan.json`. OSRM must be running.

## Web (`web/`)

A FastAPI API that streams the agent's progress over SSE, and a Next.js web app with the itinerary, the map
(MapLibre + OpenFreeMap), a card for each place, GPX and PDF export, a share link and plan adjustment
(remove places or ask for changes). The interface is available in Spanish, English or French.

```bash
cd backend && uv run --extra semantica navarra-api   # http://localhost:8000/docs
cd web && npm install && npm run dev                 # http://localhost:3000
```

## Deployment

A static demo on Vercel with 9 sample plans generated locally with Groq (`openai/gpt-oss-120b`)
and OSRM: [navarra-trip-agent-pearl.vercel.app](https://navarra-trip-agent-pearl.vercel.app) (`web/README.md`).

The full deployment (OSRM, API, web and Caddy with HTTPS in Docker, with the LLM through Groq's free API) targets
a free ARM VM on Oracle Cloud; the guide is in [`infra/oracle/README.md`](infra/oracle/README.md).
It is not live: Oracle had no free ARM capacity when it was set up.

## Evaluation

`backend/eval/peticiones.csv`: 87 requests (60 regular ones in Spanish, English and French, 15 impossible or ambiguous,
and 12 in Basque). Code validators, an LLM judge (qwen2.5:7b) calibrated against 20 hand-scored plans,
and an embedding comparison over 30 queries (`navarra-eval plan | juez | calibrar |
embeddings | informe`). Results with qwen2.5:7b running locally (8 GB RTX), October 2026:

| | Regular (60) | Impossible and ambiguous (15) | Basque (12) |
|---|---|---|---|
| Expected behaviour (plan, question or warning) | 100% | 100% | 83% |
| Interpretation: days · base town · transport | 100 · 100 · 100% | 100 · 90 · 100% | 100 · 82 · 100% |
| Places that actually exist | 100% | 100% | 100% |
| Constraints (travel, schedule, stops, no repeats) | 100% | 100% | 100% |
| Language of the text | 100% | 100% | 100% |
| Interest coverage | 0.81 | – | 0.78 |
| Judge (usefulness · coherence · faithfulness · writing, 1-5) | 4.0 · 4.2 · 4.0 · 4.0 | 3.4 · 3.8 · 3.4 · 3.6 | 4.0 · 4.6 · 4.2 · 4.2 |
| Mean latency | 14 s | 7 s | 15 s |

- **Calibrated judge**: against 20 hand-scored plans, a mean difference of 0.35-0.7 points and
  85-100% of scores within ±1, depending on the criterion.
- **Models**: llama3.1:8b reached 92% expected behaviour with a 130 s mean (it does not fit in
  8 GB with its context); qwen2.5:7b reached 100% and 14 s.
- **Embeddings**: multilingual-e5-base, recall@5 0.70 and MRR 0.90, versus 0.59 and 0.83 for e5-small.
- **What the evaluation forced me to change**: language and dates are detected in code; the
  text is assembled by code (the model put the trip home after dinner or skipped places);
  token and context caps so qwen would not loop; "impossible" plans now warn
  (interests too far away, dates beyond the forecast, more than 7 days).

## Routing (OSRM)

```powershell
mkdir infra\osrm
Invoke-WebRequest https://download.geofabrik.de/europe/spain/navarra-latest.osm.pbf -OutFile infra\osrm\navarra-latest.osm.pbf
docker compose -f infra/docker-compose.yml up -d
curl.exe "http://localhost:5000/route/v1/driving/-1.6432,42.8125;-1.6498,42.4797?overview=false"   # Pamplona -> Olite
```

Driving on `:5000`, walking on `:5001`.

## Data

Source: [datosabiertos.navarra.es](https://datosabiertos.navarra.es), licensed CC BY 4.0: art and monuments, natural areas,
lodging and farm stays, restaurants and activity companies from the Tourism Registry, birdwatching,
visitor numbers at tourist sites, and tourist offices (IDENA). Official SL-NA and PR-NA trails: Navarre Mountain Sports
and Climbing Federation via [IDENA](https://idena.navarra.es), CC BY 4.0. Descriptions: Wikipedia (CC BY-SA)
and Wikidata (CC0); places missing from the official datasets are in `recursos_extra.csv`, each with its source.
Geocoding of lodging and restaurants: [CartoCiudad](https://www.cartociudad.es) (IGN), CC BY 4.0.
Routes, map, peaks, bars and wineries: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, ODbL.
