# API para el servidor (infra/oracle). Los datos (navarra.duckdb, índice, modelo) se montan en /app/data.
FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# solo las dependencias de la API (sin los grupos dev y local)
RUN uv export --frozen --no-dev --no-default-groups --no-hashes --no-emit-project -o req.txt \
    && uv pip install --system --no-cache -r req.txt
COPY src ./src
RUN uv pip install --system --no-cache --no-deps .
EXPOSE 8000
CMD ["uvicorn", "navarra_trip.api:app", "--host", "0.0.0.0", "--port", "8000"]
