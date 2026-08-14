FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip
COPY pyproject.toml README.md alembic.ini ./
COPY catalog_app ./catalog_app
COPY migrations ./migrations
RUN pip install --no-cache-dir . && \
    useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app

USER appuser
CMD ["uvicorn", "catalog_app.main:app", "--host", "0.0.0.0", "--port", "8201"]
