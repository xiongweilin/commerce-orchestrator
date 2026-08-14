FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface

WORKDIR /app
RUN pip install --upgrade pip && \
    pip install \
      'alembic>=1.13,<2' 'fastapi>=0.115,<1' 'httpx>=0.27,<1' \
      'numpy>=1.26,<3' 'pandas>=2.2,<3' 'prometheus-client>=0.21,<1' \
      'psycopg[binary]>=3.2,<4' 'pydantic>=2.10,<3' 'pydantic-settings>=2.7,<3' \
      'python-multipart>=0.0.20,<1' 'scikit-learn>=1.5,<2' \
      'sqlalchemy>=2.0,<3' 'tenacity>=9,<10' 'uvicorn[standard]>=0.34,<1' && \
    pip install --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install 'sentence-transformers>=3.3,<4'

COPY pyproject.toml README.md alembic.ini ./
COPY feedback_app ./feedback_app
COPY tools ./tools
COPY migrations ./migrations
COPY data ./data
COPY artifacts ./artifacts
RUN pip install --no-deps .

RUN mkdir -p /models/huggingface && \
    useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app /models
USER appuser

CMD ["uvicorn", "feedback_app.main:app", "--host", "0.0.0.0", "--port", "8101"]
