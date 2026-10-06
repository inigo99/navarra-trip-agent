# API para el servidor (infra/oracle). Los datos (navarra.duckdb, lancedb) se montan en /app/data.
FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# torch de CPU: el de PyPI arrastra CUDA (varios GB) y el servidor no tiene GPU
RUN uv export --frozen --extra semantica --no-dev --no-hashes --no-emit-project -o req.txt \
    && grep -E '^torch==' req.txt > torch.txt \
    && grep -vE '^(torch|triton|nvidia-)' req.txt > resto.txt \
    && uv pip install --system --no-cache -r torch.txt --index https://download.pytorch.org/whl/cpu \
    && uv pip install --system --no-cache --no-deps -r resto.txt
COPY src ./src
RUN uv pip install --system --no-cache --no-deps .
ENV HF_HOME=/app/data/hf
EXPOSE 8000
CMD ["uvicorn", "navarra_trip.api:app", "--host", "0.0.0.0", "--port", "8000"]
