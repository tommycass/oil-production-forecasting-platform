# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

REST API for hydrocarbon production forecasting built with FastAPI. All business data is currently mocked — the `api/app/services/` layer generates synthetic data with no real database.

## Common Commands

All commands run from the `api/` directory unless noted.

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Run the API (requires API_KEY env var)
API_KEY=your_key uvicorn app.main:app --reload

# Lint (production code only — matches CI)
ruff check app/

# Run all tests
API_KEY=test-key pytest tests/ -v

# Run a single test file
API_KEY=test-key pytest tests/test_forecast.py -v

# Run a single test
API_KEY=test-key pytest tests/test_forecast.py::test_forecast_valid -v
```

### Docker (from repo root)

```bash
# Start all services (API + Prometheus + Grafana)
docker compose -f infra/docker-compose.yml up --build

# API: http://localhost:8000
# Prometheus: http://localhost:9090
# Grafana: http://localhost:3000 (admin/admin)
```

### Authentication

All endpoints except `GET /health` and `GET /metrics` require the header:
```
X-API-Key: <value of API_KEY env var>
```

## Architecture

### Service Layout

```
api/app/
├── main.py          # App factory, router registration, Prometheus setup
├── core/
│   └── security.py  # verify_api_key() FastAPI dependency
├── routes/          # health, wells, forecast routers
├── services/        # Mock data generation (wells list, forecast linear decline)
└── schemas/         # Pydantic response models
```

### Request Flow

HTTP request → route handler → `verify_api_key` dependency (Header check) → service function → Pydantic schema → response

### Monitoring Stack

- Prometheus scrapes `/metrics` every 15s; alert rules in `monitoring/alerts.yml`
- Grafana reads from Prometheus; dashboard provisioned via `monitoring/grafana/`
- `/metrics` is **excluded** from Prometheus instrumentator tracking (ADR-003) to avoid inflating business metrics

### CI/CD

GitHub Actions (`.github/workflows/ci.yml`) runs two sequential jobs on push/PR to `develop` or `main`:
1. **test**: `ruff check api/app/` then `pytest api/tests/ -v` with `API_KEY` injected
2. **build**: `docker build -f infra/Dockerfile api/` (only if tests pass)

### Git Workflow

GitFlow: feature branches → `develop` → `main`. Branch naming: `feature/`, `fix/`, `docs/`.

## Key Constraints

- The `API_KEY` env var must be set for both the running service and test execution.
- `ruff` is only linted against `app/` (not `tests/`) — this matches the CI configuration.
- The Dockerfile context is `api/` with the file at `infra/Dockerfile`: `docker build -f infra/Dockerfile api/`.


## Consigna y requerimientos

Ver `docs/consigna-fase1.md` para el resumen de entregables y requerimientos de la Fase 1.
Los PDFs con mayor detalle están en `docs/`.