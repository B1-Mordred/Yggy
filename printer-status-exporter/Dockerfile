FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/
COPY printer_exporter /app/printer_exporter
RUN pip install --no-cache-dir .

EXPOSE 8091
CMD ["uvicorn", "printer_exporter.main:app", "--host", "0.0.0.0", "--port", "8091"]
