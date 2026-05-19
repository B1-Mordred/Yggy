FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/
COPY exporter /app/exporter
RUN pip install --no-cache-dir .

EXPOSE 8090
CMD ["uvicorn", "exporter.main:app", "--host", "0.0.0.0", "--port", "8090"]
