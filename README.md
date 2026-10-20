# navarra-trip-agent

An agent that plans multi-day trips around Navarre (Spain) using the Government of Navarre's open data:
a day-by-day plan with real travel times, a map, lodging and restaurants from the official tourism registry, and the source of every place.

🇪🇸 *¿Buscas la versión en español? Está en [`README.es.md`](README.es.md).*

**Demo: [navarra-trip-agent.vercel.app](https://navarra-trip-agent.vercel.app)**: ask for your own plan ("2 days in Olite,
castles and wine", "pintxos bars in Estella"), within a daily quota, or browse the sample plans.

**Stack**: Python, LangGraph, FastAPI, DuckDB, ONNX Runtime (multilingual-e5), OSRM, MCP · Next.js,
MapLibre · Vercel, Upstash Redis, Groq · evaluation with code validators and a calibrated LLM judge.

## Layout

- `backend/`: Python 3.12 + uv (ingestion, tools, agent, MCP server and API).
- `web/`: Next.js 16 + TypeScript + Tailwind + MapLibre.
- `infra/`: OSRM in Docker, deployment on Vercel and an alternative for a VM.
- `skills/`: a skill so other agents can use the MCP server.

## Development

```bash
cd backend
uv sync
uv run pytest
uv run navarra-descargar          # the 4 v1 datasets into data/raw/ (cached)
uv run navarra-descargar --force  # download again
uv run navarra-normalizar         # data/navarra.duckdb (geocodes with CartoCiudad, cached)
uv run navarra-mapa               # data/mapa.html: control map with every point
uv run navarra-indexar            # semantic index: multilingual-e5-base (ONNX fp32) in data/indice_*.npz
uv run navarra-tiempos            # travel-time matrices for deploying without OSRM (OSRM running)
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
      "args": ["--directory", "C:\\path\\navarra-trip-agent\\backend", "run", "navarra-mcp"]
    }
  }
}
```

### Skill

[`skills/navarra-trip-planner/SKILL.md`](skills/navarra-trip-planner/SKILL.md) tells an agent (Claude Code, Claude Desktop…) when to use these tools and how: options vs. day-by-day plans, real travel times with `ruta`, and no invented hours, prices or phone numbers. Copy the folder to `~/.claude/skills/` or to your project's `.claude/skills/`.

## Planning agent

Built with LangGraph. The LLM only interprets the request and writes one or two sentences per place.
Everything else is deterministic code (`planner.py`), and the final text is assembled by code:

- **Candidates** around the base town (30 km by car, 4 km on foot), taking turns between the ranking
  of each interest (semantic search with multilingual-e5-base). Hiking trails only if asked for.
- **Days** built with real OSRM travel times: each interest takes a turn opening a day, and the place
  that adds the least to the return trip is added next, until the end time set by the pace (17:30, 19:00 or 20:00).
  Places in the base town are visited together, on the same day, and what is left of them fills short afternoons.
  Walks and hours on foot are capped; when it rains, monuments are picked.
- **Order** is exactly optimal (Held-Karp), with times rounded to the quarter hour, lunch (or a picnic on the way),
  longer visits if the afternoon has room, dinner or a pintxos round, and lodging (if there is an overnight stay).
- **Options without an itinerary** ("a mountain walk near Isaba", "pintxos bars in Estella"):
  up to 6 places, trails, bars or restaurants with their duration and distance.
- **Interpretation** with safeguards in code: language, relative dates ("on Saturday", "in two months"),
  transport and pace only if the request states them, and town names with aliases and typos.

```powershell
ollama pull qwen2.5:7b
uv run navarra-plan "3 días en Estella, me gusta el románico y la naturaleza"
$env:NAVARRA_LLM = "groq:openai/gpt-oss-120b"   # optional, with GROQ_API_KEY (free)
```

The full plan (with GeoJSON) is written to `data/plan.json`. OSRM must be running.

## Web (`web/`)

A FastAPI API that streams the agent's progress over SSE, and a Next.js web app with the itinerary, the map
(MapLibre + OpenFreeMap), a card for each place, GPX and PDF export, a share link and plan adjustment
(remove places, which keeps the other days as they were, or ask for changes). The interface is available in Spanish, English or French.

```bash
cd backend && uv run navarra-api          # http://localhost:8000/docs
cd web && npm install && npm run dev      # http://localhost:3000
```

## Deployment

Everything runs on Vercel's free plan ([`infra/vercel/README.md`](infra/vercel/README.md)):

- **Web**: Next.js, calling the API directly (CORS).
- **API**: FastAPI as a Python function (SSE streaming, up to 5 minutes per plan). The LLM is
  `openai/gpt-oss-120b` through Groq's free API.
- **No routing server**: travel times and distances between every place and base town are
  precomputed with the local OSRM (`navarra-tiempos`, ~690 points, a few MB); the route line on the
  map comes from FOSSGIS's public OSRM.
- **Semantic search without torch**: multilingual-e5-base in ONNX with onnxruntime (index in fp32,
  built once locally; queries in fp16) and an exact numpy search over the ~220 places. It replaced
  LanceDB and sentence-transformers, which do not fit in a function.
- **State in Upstash Redis**: shared plans (90 days) and limits per IP and per day, because the free
  Groq key allows about 55 plans a day.

The 9 sample plans (`web/public/ejemplos`, `navarra-demo`) still work without the API, and
`NEXT_PUBLIC_DEMO=1` builds the static version. [`infra/oracle/`](infra/oracle/README.md) has a
Docker alternative for a VM (OSRM, API, web and Caddy).

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
- **Models** (regular requests unless stated):

  | | qwen2.5:7b | qwen2.5:3b | Groq gpt-oss-120b | llama3.1:8b | qwen2.5:1.5b |
  |---|---|---|---|---|---|
  | Expected behaviour | 100% | 100% | 100% | 92% | 97% |
  | Language of the text | 100% | 100% | 100% | 69% | 93% |
  | Judge (usefulness · coherence · faithfulness · writing) | 4.0 · 4.2 · 4.0 · 4.0 | 3.9 · 4.2 · 4.0 · 4.0 | 4.0 · 4.3 · 4.0 · 4.0 | – | – |
  | Impossible · Basque (behaviour) | 100 · 83% | 87 · 100% | 93 · 100% | 87 · 67% | 87 · 67% |
  | Mean latency | 14 s | 11 s | 24 s | 130 s | 29 s (loops up to 11 min) |

  The 3b and Groq were re-run after fixing Basque town names and "weekend"; the other models are from before those fixes.
  A model 17 times larger (gpt-oss-120b) does not beat the 7B: code assembles the plan and the LLM
  only interprets the request and writes sentences. Groq is for hosting it without a GPU (about 55 free requests a day).

- **Embeddings** (30 queries over 218 places, `navarra-eval embeddings`): multilingual-e5-base gets
  recall@5 0.64 and MRR 0.87, versus 0.56 and 0.75 for e5-small. Queries in fp16 lose nothing
  against fp32 and use half the memory; int8 drops to 0.62 / 0.83 for queries only and to
  0.58 / 0.78 for index and queries. (The earlier 0.70 / 0.90 was over 156 places, before trails
  and wineries were added.)
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
`backend/data/` includes the database, the semantic index and the travel-time matrices that the deployed API uses.
