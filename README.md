# navarra-trip-agent

Agente que diseña escapadas de varios días por Navarra con los datos abiertos del Gobierno de Navarra:
plan por días con tiempos reales, mapa, alojamientos y restaurantes del Registro de Turismo y la fuente de cada lugar.

> Estado: semanas 1-6 (datos, herramientas + MCP, agente, evaluación, web y despliegue en Oracle Cloud con Groq).

## Estructura

- `backend/`: Python 3.12 + uv (ingesta, herramientas, agente, servidor MCP y API).
- `web/`: Next.js 16 + TypeScript + Tailwind + MapLibre.

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
`bares_cerca`, `recurso`, `actividades_cerca`, `oficinas_turismo`, `aves`, `ruta`, `prevision_tiempo` y `conjuntos`. En Claude Desktop
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

## Agente planificador

LangGraph. El LLM solo interpreta la petición y escribe una o dos frases por lugar; todo lo
demás es código determinista (`planner.py`) y el texto lo compone el código:

- **Candidatos** alrededor de la base (30 km en coche, 4 a pie), alternando el ranking de cada
  interés (búsqueda semántica con multilingual-e5-base); senderos solo si se piden.
- **Días** con tiempos reales de OSRM: cada día lo abre por turnos un interés y se añade lo que
  menos alarga la vuelta, hasta la hora de fin del ritmo (17:30, 19:00 o 20:00). Lo del pueblo
  base se ve seguido y en su día; tope de caminatas y de horas andando; con lluvia, monumentos.
- **Orden** óptimo exacto (Held-Karp), horas al cuarto de hora, comida (o picnic en ruta),
  visitas alargadas si sobra tarde, cena o ronda de pintxos y alojamientos (si hay noche).
- **Opciones sin itinerario** ("una ruta de monte cerca de Isaba", "bares de pintxos en
  Estella"): hasta 6 lugares, rutas, bares o restaurantes con su duración y distancia.
- **Interpretación** con salvaguardas en código: idioma, fechas relativas ("el sábado", "dentro
  de dos meses"), transporte y ritmo solo si la petición los dice, pueblos con alias y erratas.

```powershell
ollama pull qwen2.5:7b
uv run --extra semantica navarra-plan "3 días en Estella, me gusta el románico y la naturaleza"
$env:NAVARRA_LLM = "groq:openai/gpt-oss-120b"   # opcional, con GROQ_API_KEY (gratis)
```

El plan completo (con GeoJSON) queda en `data/plan.json`. Necesita OSRM levantado.

## Web (`web/`)

API FastAPI con el progreso del agente por SSE y una web Next.js con el itinerario, el mapa
(MapLibre + OpenFreeMap), la ficha de cada lugar, GPX, PDF, enlace para compartir y ajuste del
plan (quitar lugares o pedir cambios). Interfaz en español, inglés o francés.

```bash
cd backend && uv run --extra semantica navarra-api   # http://localhost:8000/docs
cd web && npm install && npm run dev                 # http://localhost:3000
```

## Despliegue

Demo en una VM ARM gratuita de Oracle Cloud (OSRM, API, web y Caddy con HTTPS en Docker) con el
LLM por la API gratuita de Groq: guía en [`infra/oracle/README.md`](infra/oracle/README.md).
Sin servidor, la web también se exporta como estática con planes de ejemplo (`web/README.md`).

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
- **Modelos**: llama3.1:8b quedó en 92 % de comportamiento esperado y 130 s de media (no cabe en
  8 GB con su contexto); qwen2.5:7b, 100 % y 14 s.
- **Embeddings**: multilingual-e5-base, recall@5 0,70 y MRR 0,90, frente a 0,59 y 0,83 de e5-small.
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
