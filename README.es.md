# navarra-trip-agent

Agente que diseña escapadas de varios días por Navarra con los datos abiertos del Gobierno de Navarra:
plan por días con tiempos reales, mapa, alojamientos y restaurantes del Registro de Turismo y la fuente de cada lugar.

🇬🇧 *Looking for the English version? See [`README.md`](README.md).*

**Demo: [navarra-trip-agent.vercel.app](https://navarra-trip-agent.vercel.app)**: pide tu plan ("2 días en Olite,
castillos y vino", "bares de pintxos en Estella"), con un cupo diario, o mira los planes de ejemplo.

**Tecnologías**: Python, LangGraph, FastAPI, DuckDB, ONNX Runtime (multilingual-e5), OSRM, MCP ·
Next.js, MapLibre · Vercel, Upstash Redis, Groq · evaluación con validadores en código y juez LLM
calibrado.

## Estructura

- `backend/`: Python 3.12 + uv (ingesta, herramientas, agente, servidor MCP y API).
- `web/`: Next.js 16 + TypeScript + Tailwind + MapLibre.
- `infra/`: OSRM en Docker, despliegue en Vercel y una alternativa para una VM.
- `skills/`: un skill para que otros agentes usen el servidor MCP.

## Desarrollo

```bash
cd backend
uv sync
uv run pytest
uv run navarra-descargar          # 4 conjuntos de la v1 a data/raw/ (con caché)
uv run navarra-descargar --force  # volver a descargar
uv run navarra-normalizar         # data/navarra.duckdb (geolocaliza con CartoCiudad, con caché)
uv run navarra-mapa               # data/mapa.html: mapa de control con todos los puntos
uv run navarra-indexar            # índice semántico: multilingual-e5-base (ONNX fp32) en data/indice_*.npz
uv run navarra-tiempos            # matrices de tiempos para desplegar sin OSRM (OSRM levantado)
```

## Servidor MCP (`navarra-opendata-mcp`)

Herramientas: `buscar_recursos`, `recursos_cerca`, `alojamientos_cerca`, `restaurantes_cerca`,
`bares_cerca`, `recurso`, `actividades_cerca`, `oficinas_turismo`, `aves`, `ruta`, `prevision_tiempo` y `conjuntos`. En Claude Desktop
(`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "navarra-opendata": {
      "command": "uv",
      "args": ["--directory", "C:\\ruta\\navarra-trip-agent\\backend", "run", "navarra-mcp"]
    }
  }
}
```

### Skill

[`skills/navarra-trip-planner/SKILL.md`](skills/navarra-trip-planner/SKILL.md) le dice a un agente (Claude Code, Claude Desktop…) cuándo usar estas herramientas y cómo: opciones o plan por días, tiempos reales con `ruta` y sin inventar horarios, precios ni teléfonos. Copia la carpeta a `~/.claude/skills/` o al `.claude/skills/` de tu proyecto.

## Agente planificador

LangGraph. El LLM solo interpreta la petición y escribe una o dos frases por lugar; todo lo
demás es código determinista (`planner.py`) y el texto lo compone el código:

- **Candidatos** alrededor de la base (30 km en coche, 4 a pie), alternando el ranking de cada
  interés (búsqueda semántica con multilingual-e5-base); senderos solo si se piden.
- **Días** con tiempos reales de OSRM: cada día lo abre por turnos un interés y se añade lo que
  menos alarga la vuelta, hasta la hora de fin del ritmo (17:30, 19:00 o 20:00). Lo del pueblo
  base se ve seguido y en su día, y lo que sobra rellena las tardes cortas; tope de caminatas y de
  horas andando; con lluvia, monumentos.
- **Orden** óptimo exacto (Held-Karp), horas al cuarto de hora, comida (o picnic en ruta),
  visitas alargadas si sobra tarde, cena o ronda de pintxos y alojamientos (si hay noche).
- **Opciones sin itinerario** ("una ruta de monte cerca de Isaba", "bares de pintxos en
  Estella"): hasta 6 lugares, rutas, bares o restaurantes con su duración y distancia.
- **Interpretación** con salvaguardas en código: idioma, fechas relativas ("el sábado", "dentro
  de dos meses"), transporte y ritmo solo si la petición los dice, pueblos con alias y erratas.

```powershell
ollama pull qwen2.5:7b
uv run navarra-plan "3 días en Estella, me gusta el románico y la naturaleza"
$env:NAVARRA_LLM = "groq:openai/gpt-oss-120b"   # opcional, con GROQ_API_KEY (gratis)
```

El plan completo (con GeoJSON) queda en `data/plan.json`. Necesita OSRM levantado.

## Web (`web/`)

API FastAPI con el progreso del agente por SSE y una web Next.js con el itinerario, el mapa
(MapLibre + OpenFreeMap), la ficha de cada lugar, GPX, PDF, enlace para compartir y ajuste del
plan (quitar lugares, que deja igual los demás días, o pedir cambios). Interfaz en español, inglés
o francés.

```bash
cd backend && uv run navarra-api          # http://localhost:8000/docs
cd web && npm install && npm run dev      # http://localhost:3000
```

## Despliegue

Todo en el plan gratuito de Vercel ([`infra/vercel/README.md`](infra/vercel/README.md)):

- **Web**: Next.js, que llama directamente a la API (CORS).
- **API**: FastAPI como función de Python (SSE, hasta 5 minutos por plan). El LLM es
  `openai/gpt-oss-120b` por la API gratuita de Groq.
- **Sin servidor de rutas**: los tiempos y distancias entre todos los lugares y pueblos base se
  precalculan con el OSRM local (`navarra-tiempos`, ~690 puntos, unos MB); la línea de la ruta en el
  mapa sale del OSRM público de FOSSGIS.
- **Búsqueda semántica sin torch**: multilingual-e5-base en ONNX con onnxruntime (índice en fp32,
  hecho una vez en local; consultas en fp16) y búsqueda exacta en numpy sobre los ~220 lugares.
  Sustituye a LanceDB y sentence-transformers, que no caben en una función.
- **Estado en Upstash Redis**: planes compartidos (90 días) y límites por IP y por día, porque la
  clave gratuita de Groq da para unos 55 planes diarios.

Los 9 planes de ejemplo (`web/public/ejemplos`, `navarra-demo`) siguen funcionando sin la API, y con
`NEXT_PUBLIC_DEMO=1` se genera la versión estática.

[`infra/oracle/`](infra/oracle/README.md) tiene una alternativa en Docker para una VM (OSRM, API,
web y Caddy).

## Evaluación

`backend/eval/peticiones.csv`: 87 peticiones (60 normales en es/en/fr, 15 imposibles o ambiguas
y 12 en euskera). Validadores en código, juez LLM (qwen2.5:7b) calibrado con 20 planes puntuados a
mano y comparación de embeddings con 30 consultas (`navarra-eval plan | juez | calibrar |
embeddings | informe`). Resultados con qwen2.5:7b en local (RTX de 8 GB), octubre de 2026:

| | Normales (60) | Imposibles y ambiguas (15) | Euskera (12) |
|---|---|---|---|
| Comportamiento esperado (plan, pregunta o aviso) | 100 % | 100 % | 83 % |
| Interpretación: días · base · transporte | 100 · 100 · 100 % | 100 · 90 · 100 % | 100 · 82 · 100 % |
| Lugares que existen de verdad | 100 % | 100 % | 100 % |
| Restricciones (trayecto, horario, paradas, sin repetir) | 100 % | 100 % | 100 % |
| Idioma del texto | 100 % | 100 % | 100 % |
| Cobertura de intereses | 0,81 | – | 0,78 |
| Juez (utilidad · coherencia · fidelidad · redacción, 1-5) | 4,0 · 4,2 · 4,0 · 4,0 | 3,4 · 3,8 · 3,4 · 3,6 | 4,0 · 4,6 · 4,2 · 4,2 |
| Latencia media | 14 s | 7 s | 15 s |

- **Juez calibrado**: frente a 20 planes puntuados a mano, diferencia media de 0,35-0,7 puntos y
  85-100 % de notas a ±1 según el criterio.
- **Modelos** (peticiones normales salvo que se indique):

  | | qwen2.5:7b | qwen2.5:3b | Groq gpt-oss-120b | llama3.1:8b | qwen2.5:1.5b |
  |---|---|---|---|---|---|
  | Comportamiento esperado | 100 % | 100 % | 100 % | 92 % | 97 % |
  | Idioma del texto | 100 % | 100 % | 100 % | 69 % | 93 % |
  | Juez (utilidad · coherencia · fidelidad · redacción) | 4,0 · 4,2 · 4,0 · 4,0 | 3,9 · 4,2 · 4,0 · 4,0 | 4,0 · 4,3 · 4,0 · 4,0 | – | – |
  | Imposibles · euskera (comportamiento) | 100 · 83 % | 87 · 100 % | 93 · 100 % | 87 · 67 % | 87 · 67 % |
  | Latencia media | 14 s | 11 s | 24 s | 130 s | 29 s (bucles de hasta 11 min) |

  El 3b y Groq se repitieron tras corregir los pueblos en euskera y "fin de semana"; los demás modelos son de antes.
  Un modelo 17 veces mayor (gpt-oss-120b) no mejora al 7B: el código compone el plan y el LLM
  solo interpreta y escribe frases. Groq sirve para alojarlo sin GPU (unas 55 peticiones al día gratis).

- **Embeddings** (30 consultas sobre 218 lugares, `navarra-eval embeddings`): multilingual-e5-base
  da recall@5 0,64 y MRR 0,87, frente a 0,56 y 0,75 de e5-small. Las consultas en fp16 no pierden
  nada frente a fp32 y usan la mitad de memoria; con int8 baja a 0,62 / 0,83 solo en la consulta y a
  0,58 / 0,78 en índice y consulta. (El 0,70 / 0,90 anterior era sobre 156 lugares, antes de añadir
  senderos y bodegas.)
- **Lo que la evaluación obligó a cambiar**: el idioma y las fechas se detectan en código; el
  texto lo compone el código (el modelo ponía la vuelta después de la cena o se saltaba lugares);
  tope de tokens y contexto para que qwen no entrara en bucle; los planes "imposibles" avisan
  (intereses lejos, fechas fuera de previsión, más de 7 días).

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
afluencia a recursos turísticos y oficinas de turismo (IDENA). Senderos homologados SL-NA y PR-NA: Federación Navarra
de Deportes de Montaña y Escalada vía [IDENA](https://idena.navarra.es), CC BY 4.0. Descripciones: Wikipedia (CC BY-SA)
y Wikidata (CC0); lugares que faltan en los conjuntos oficiales, en `recursos_extra.csv` con su fuente.
Geolocalización de alojamientos y restaurantes: [CartoCiudad](https://www.cartociudad.es) (IGN), CC BY 4.0.
Rutas, mapa, cimas, bares y bodegas: © colaboradores de [OpenStreetMap](https://www.openstreetmap.org/copyright), ODbL.
`backend/data/` incluye la base de datos, el índice semántico y las matrices de tiempos que usa la API desplegada.
