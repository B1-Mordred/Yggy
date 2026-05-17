FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY bridge /app/bridge

RUN pip install --no-cache-dir .

CMD ["python", "-m", "bridge.main"]
