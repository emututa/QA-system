# Zimworx DevOps Quality Assurance Platform

Production-oriented foundation for centralized QA across applications. Phase 1 provides application and team management, JWT authentication, RBAC-ready organization boundaries, asynchronous Celery jobs, discovery/run/test models, a live Next.js dashboard, PostgreSQL, Redis, Docker Compose, and Prometheus/Grafana observability scaffolding.

## Architecture

- `backend/`: Django REST Framework API and Celery task definitions.
- `frontend/`: Next.js TypeScript dashboard.
- `docker-compose.yml`: local platform dependencies and services.
- `prometheus.yml`: Prometheus scrape configuration.
- `grafana/`: Grafana provisioning.
- `.github/workflows/`: CI quality checks.

## Phase 1 MVP

- Organization, team, role and user-profile boundaries.
- Application and environment registration.
- External staging URL validation and SSRF network guards.
- Discovery, test suite, test run, evidence and quality gate data models.
- JWT authentication and organization-scoped application APIs.
- Celery/Redis orchestration scaffolding.
- Health and Prometheus metrics endpoints.
- Responsive enterprise dashboard consuming live API totals.

## Run locally

```powershell
docker compose up --build
```

Open the dashboard at `http://localhost:3000`, API at `http://localhost:8000`, Prometheus at `http://localhost:9090`, and Grafana at `http://localhost:3001`.

For direct backend development:

```powershell
cd backend
C:/Users/emmanuel.mututa/AppData/Local/Python/pythoncore-3.15-64/python.exe manage.py migrate
C:/Users/emmanuel.mututa/AppData/Local/Python/pythoncore-3.15-64/python.exe manage.py runserver
```

## API

- `POST /api/auth/token/` — obtain a JWT.
- `GET /api/applications/` — list organization applications.
- `POST /api/applications/` — register an application.
- `GET /api/dashboard/` — live summary totals.
- `GET /api/health/` — service health.
- `GET /metrics` — Prometheus metrics.

## Security boundaries

Application URLs are validated before use. Loopback, link-local, private and reserved address ranges are rejected. Credentials remain separate from URLs and must be stored encrypted by the secret-management implementation before production use. Production execution is restricted by environment policy and is never destructive by default.
