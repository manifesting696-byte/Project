FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PROJECT_ROOT=/app
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY docs ./docs
COPY config ./config
COPY dataset ./dataset
COPY semantic.yaml schema.sql ./
COPY semantic_versions ./semantic_versions
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app app && mkdir -p /app/runtime && chown app:app /app/runtime
USER app
EXPOSE 8000
CMD ["uvicorn", "enterprise_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
