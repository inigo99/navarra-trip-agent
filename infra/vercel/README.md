# Despliegue en Vercel

Dos proyectos de Vercel sobre el mismo repo, los dos en el plan gratuito:

```
navegador ──▶ web (Next.js, raíz web/) ──CORS──▶ API (FastAPI, raíz backend/) ──▶ Groq (LLM)
                                                   │  DuckDB, tiempos e índice: archivos de la función
                                                   └▶ Upstash Redis (planes y límites)
```

La API no necesita servidores propios: los tiempos de OSRM están precalculados
(`data/tiempos.npz`), la búsqueda semántica usa e5 en ONNX y los planes se guardan en Redis.

## 1. Datos (en tu equipo, con OSRM levantado)

```powershell
docker compose -f infra/docker-compose.yml up -d
cd backend
uv sync
uv run navarra-indexar      # data/indice_multilingual-e5-base.npz (descarga e5 en fp32, 1,1 GB)
uv run navarra-tiempos      # data/tiempos.npz: ~690 puntos, coche y a pie
uv run pytest -q
```

`navarra.duckdb`, `tiempos.npz` y el índice van en git (`.gitignore` los deja pasar): súbelos con
el código. Repite los dos comandos cada vez que regeneres la base de datos.

## 2. API

1. En Vercel: *Add New → Project*, el repo `navarra-trip-agent`, **Root Directory `backend`**.
   Detecta FastAPI (`app.py`); el build (`vercel_build.py`) descarga el modelo ONNX.
2. *Environment Variables*:
   - `GROQ_API_KEY`: tu clave de <https://console.groq.com>.
   - `VERCEL_SUPPORT_LARGE_FUNCTIONS` = `1`: la función ocupa ~840 MB (el modelo en fp16 son 555)
     y el límite normal es 500 MB.
3. *Storage → Marketplace → Upstash (Redis)*, plan gratuito, región `us-east-1` (la de las funciones
   por defecto) y *Connect* al proyecto: añade `KV_REST_API_URL` y `KV_REST_API_TOKEN`.
4. *Deploy*. Comprueba `https://<api>.vercel.app/salud` y `https://<api>.vercel.app/docs`.

El resto de la configuración está en `backend/app.py` (modelo de Groq, límites de 5 planes por IP y
hora y 45 al día, dominios con CORS); cualquier variable de entorno con el mismo nombre la sustituye.

## 3. Web

En el proyecto de la web que ya existe (raíz `web`): *Settings → Environment Variables*, borra
`NEXT_PUBLIC_DEMO` y añade `NEXT_PUBLIC_API_URL` = `https://<api>.vercel.app`. Redespliega.

Si la web tiene otro dominio, añádelo a `NAVARRA_CORS` en la API (separados por comas).

## Límites

- **Groq gratis**: ~200 000 tokens al día (unos 55 planes). Al pasar de 45 planes en el día, la web
  avisa y deja los ejemplos.
- **Primera petición tras un rato sin uso**: unos segundos más (arranca la función y carga el
  modelo).
- **Línea de la ruta en el mapa**: del OSRM público de FOSSGIS (`routing.openstreetmap.de`); si no
  responde, el plan sale igual, sin la línea.
- **Redis gratis**: 256 MB; los planes caducan a los 90 días.
