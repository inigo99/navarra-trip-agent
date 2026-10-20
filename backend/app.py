"""Entrada de la API en Vercel (Python runtime, FastAPI). Configuración de la demo pública:
lo secreto (GROQ_API_KEY y Upstash Redis) va en las variables de entorno del proyecto."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

for clave, valor in {
    "NAVARRA_LLM": "groq:openai/gpt-oss-120b",
    "NAVARRA_TIEMPOS": "data/tiempos.npz",  # sin servidor OSRM
    "NAVARRA_LIMITE_HORA": "5",  # por IP
    "NAVARRA_LIMITE_DIA": "45",  # en total: la cuota gratuita de Groq da para unos 55
    "NAVARRA_CORS": ",".join(
        f"https://{d}.vercel.app" for d in ("navarra-trip-agent", "navarra-trip-agent-pearl")
    )
    + ",http://localhost:3000",
}.items():
    os.environ.setdefault(clave, valor)

from navarra_trip.api import app  # noqa: E402

__all__ = ["app"]
