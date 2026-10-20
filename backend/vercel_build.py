"""Build de Vercel: descarga el modelo de las consultas (e5 en ONNX fp16, 555 MB) a la función."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from navarra_trip.tools.semantica import INDICE, descargar  # noqa: E402

if __name__ == "__main__":
    for f in (INDICE, "data/navarra.duckdb", "data/tiempos.npz"):
        assert os.path.exists(f), f"falta {f}: genéralo en local y súbelo (infra/vercel/README.md)"
    print("modelo en", descargar())
