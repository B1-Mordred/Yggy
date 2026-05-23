FROM node:22-alpine AS ops-ui-build

WORKDIR /ui
COPY ops-ui/package*.json ./
RUN npm ci
COPY ops-ui/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY app /app/app
COPY --from=ops-ui-build /ui/dist /app/app/ops_static

RUN pip install --no-cache-dir .

EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
