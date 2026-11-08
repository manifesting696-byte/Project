FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PROJECT_ROOT=/app
WORKDIR /app
COPY requirements.txt pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY docs ./docs
COPY config ./config
COPY dataset ./dataset
COPY semantic.yaml schema.sql ./
COPY semantic_versions ./semantic_versions
EXPOSE 8000
CMD ["uvicorn", "enterprise_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
