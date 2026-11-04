# Stage 1: build the React frontend
FROM node:20-slim AS frontend
WORKDIR /app/frontend

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./

# Fetch the OneZoom viewer bundle (© OneZoom CIO, non-commercial science use only)
RUN bash scripts/fetch-onezoom.sh

RUN npm run build


# Stage 2: Python application
FROM python:3.12-slim
WORKDIR /app

COPY backend/ backend/
RUN pip install --no-cache-dir ./backend

# Built frontend (served at / by FastAPI)
COPY --from=frontend /app/frontend/dist frontend/dist

# Database baked in at build time (run `apw ingest` locally first)
COPY data/apw.sqlite data/apw.sqlite

EXPOSE 8080
CMD ["apw", "serve", "--host", "0.0.0.0", "--port", "8080"]
