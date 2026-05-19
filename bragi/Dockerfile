FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY bragi /app/bragi

RUN pip install --no-cache-dir .

EXPOSE 8650
CMD ["uvicorn", "bragi.main:app", "--host", "0.0.0.0", "--port", "8650"]
