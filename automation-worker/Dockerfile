FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY worker /app/worker

RUN pip install --no-cache-dir .

CMD ["python", "-m", "worker.main"]
