FROM ghcr.io/astral-sh/uv:0.11.2 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" UV_LINK_MODE=copy \
    DATABASE_PATH=/app/data/runtime/signaldesk.db MODEL_DIR=/app/artifacts/models
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev && useradd --create-home --uid 10001 app
COPY --chown=app:app signaldesk ./signaldesk
COPY --chown=app:app dashboard ./dashboard
COPY --chown=app:app config ./config
COPY --chown=app:app data/datasets ./data/datasets
COPY --chown=app:app .streamlit ./.streamlit
RUN mkdir -p data/runtime artifacts/models && chown -R app:app data artifacts
USER app
EXPOSE 8000 8501
CMD ["uvicorn", "signaldesk.api:app", "--host", "0.0.0.0", "--port", "8000"]
