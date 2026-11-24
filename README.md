# Zimworx DevOps QA

Zimworx QA is a central dashboard for registering applications by GitHub repository and authorized staging URL. The frontend is a React SPA built with Vite; the API and data services are Django REST Framework, PostgreSQL, Redis and Celery.

## Run locally

Start Docker Desktop, then from the repository root:

```powershell
docker compose up --build -d
docker compose exec backend python manage.py createsuperuser
```

Open the dashboard at `http://localhost:3003`. Sign in with the administrator account you created. On first use, the administrator can set the organization and team in the application registration form. Grafana is at `http://localhost:3002`; Prometheus is at `http://localhost:9090`.

If you do not already have a local `.env`, copy `.env.example` to `.env` and set a strong `DJANGO_SECRET_KEY`. Do not overwrite an existing `.env`. To inspect private GitHub repositories, configure `GITHUB_TOKEN` with a read-only fine-grained token that has repository metadata and contents read access. Never put credentials in the repository URL.

AI settings (`AI_ENABLED`, `AI_PROVIDER`, `OPENAI_API_KEY`, and `OPENAI_MODEL`) are passed only to the backend and trusted Celery worker from `.env`. Do not expose provider keys to browser code or repository test containers. The AI modules are currently scaffolds and do not yet call a provider.

## Register and assess an application

1. Sign in to the dashboard and select **Register application**.
2. Enter the application name, a `https://github.com/owner/repository` URL, the branch to inspect, and its authorized staging URL. Technology stack and description are optional.
3. Select **Register application**. The registration API validates the repository URL and rejects staging addresses in private, loopback, link-local, or reserved networks.
4. Open **Applications** and select **Run QA preflight**.
5. View the asynchronous run results on the application card.

The initial read-only assessment checks repository access and inspects top-level metadata/test setup, then sends a single HTTP `GET` request to the registered staging URL. It does not change the GitHub repository or submit forms to the staging site.

**Important scope:** this preflight does not clone a repository or execute its unit, integration, or browser tests. Repository source is untrusted; running project test commands requires a separately isolated, resource-limited runner. Playwright, Selenium, Cypress, Locust, and ZAP test execution are not enabled by this registration flow.

The Playwright image can be built and smoke-tested separately with `docker build -t zimworx-playwright workers/playwright`. This verifies the browser runtime only; it does not connect browser tests to the dashboard or target a registered staging site.

For private repositories, the optional `GITHUB_TOKEN` is available only to the API and Celery worker for GitHub read operations. Keep it in the ignored local `.env`, grant minimum read-only permissions, and restart/recreate services after changing it:

```powershell
docker compose up -d --force-recreate backend worker
```

## API

- `POST /api/auth/token/` — obtain a JWT.
- `GET /api/auth/me/` — current account and organization setup state.
- `POST /api/organizations/bootstrap/` — administrator first-run organization/team setup.
- `GET /api/applications/` — list organization applications.
- `POST /api/applications/register/` — register a GitHub repository and staging environment.
- `POST /api/applications/{id}/run-assessment/` — queue the read-only QA preflight.
- `GET /api/applications/{id}/assessment-runs/` — read assessment runs and results.
- `GET /api/dashboard/` — organization-scoped summary.
- `GET /api/monitoring/?period=24h&application=<uuid>` — monitoring summary for `24h`, `7d`, or `30d`.
- `GET /api/health/` — API health.
- `GET /metrics` — Prometheus metrics.

## Project layout

- `backend/applications/` — Django API, application registration, data models and assessment orchestration. Its Django migration label remains `core` to preserve existing database tables.
- `backend/integrations/github/` — GitHub API client and read-only repository metadata inspection.
- `backend/testing/integration/` — staging URL validation and the current read-only HTTPX availability probe.
- `backend/testing/unit/` — backend unit tests.
- `backend/testing/e2e/` — reserved for browser test modules; execution is not enabled.
- `backend/ai_engine/`, `backend/quality_gates/`, `backend/reports/`, `backend/monitoring/` — feature packages for future modules.
- `backend/config/` — Django settings, URLs and ASGI/WSGI entry points.
- `workers/` — Python, Node.js and Playwright runner image templates; they are not yet connected to Compose or used to execute repository code.
- `frontend/src/` — React application entry point and dashboard views.
- `frontend/src/styles.css` — shared dashboard styling; existing dashboard design is retained.
- `frontend/Dockerfile` — Vite production build served by Nginx with SPA route fallback.
- `docker-compose.yml` — local services.
- `prometheus.yml`, `grafana/` — observability configuration.

## Checks

```powershell
C:/Users/emmanuel.mututa/AppData/Local/Python/pythoncore-3.15-64/python.exe -m pytest -q backend/testing/unit/test_applications.py
Set-Location frontend
npm.cmd run build
```
