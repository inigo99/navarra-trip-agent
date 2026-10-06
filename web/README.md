# web

Interfaz de navarra-trip-agent: Next.js 16, Tailwind 4 y MapLibre con teselas de [OpenFreeMap](https://openfreemap.org).

```bash
# en otra terminal, la API: cd backend && uv run --extra semantica navarra-api
npm install
npm run dev   # http://localhost:3000
```

La web pasa `/api/*` a la API (`API_URL`, por defecto `http://127.0.0.1:8000`). La interfaz sale en español,
inglés o francés según el idioma del navegador; el itinerario, en el idioma de la petición.

- `/`: petición y progreso del agente (SSE).
- `/plan/[id]`: itinerario, mapa, detalle de cada lugar, GPX, PDF (imprimir), enlace para compartir y ajuste del plan.

## Demo pública (estática)

Sin servidor: la web muestra planes de ejemplo generados en local y no permite pedir ni ajustar planes.

```bash
cd backend && uv run --extra semantica navarra-demo   # escribe web/public/ejemplos (OSRM levantado; NAVARRA_LLM=groq:openai/gpt-oss-120b u Ollama)
cd web && NEXT_PUBLIC_DEMO=1 npm run build            # exportación estática en web/out
```

En Vercel: directorio raíz `web` y variable `NEXT_PUBLIC_DEMO=1`.
