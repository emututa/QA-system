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

AI settings (`AI_ENABLED`, `AI_PROVIDER`, `OPENAI_API_KEY`, and `OPENAI_MODEL`) are passed only to the backend and trusted Celery worker from `.env`. Do not expose provider keys to browser code or repository test containers. The application uses OpenAI's Responses API for opt-in test suggestions, failure analysis, and Python pytest candidate generation. Source snippets are sent to OpenAI only after a user action; the server redacts common credential patterns and disables provider response storage. Use **Test OpenAI connection** in the application card to make a small live request and verify that the configured key can access the configured model. This request is user-triggered and may count against provider usage. AI actions are limited to 10 requests per authenticated user per hour.

Generated test files are saved as **unapproved candidates**. A human must inspect the complete code and select **Approve for execution**; only then does **Run approved test** queue it. Before execution, the worker verifies that the source file still has the same GitHub blob SHA used to generate the candidate. A changed source requires a fresh candidate and review. Execution currently supports a single Python source file plus generated pytest code; it does not install dependencies from arbitrary repositories, and imports beyond the standard library, pytest, and the selected module are rejected.

Approved candidates run in a separate Python Docker container with no network, no secrets, a read-only root filesystem, a non-root user, dropped capabilities, no-new-privileges, bounded memory/CPU/PIDs, and a 45-second timeout. The candidate's selected source file is loaded from the pinned repository revision; the rest of the repository is not cloned. Test output is persisted with the test run and displayed in the dashboard. This isolated runner is deliberately not exposed on a host port and is reachable only from the Celery worker on an internal Compose network.

**Docker daemon access warning:** The `python-runner` orchestrator mounts `/var/run/docker.sock` so it can create and remove short-lived sandboxes. Access to this socket is effectively host-level Docker control. Keep the runner and worker networks private, do not expose the runner API, and do not deploy this Compose configuration on a shared/untrusted Docker host without replacing the socket-based orchestrator with a separately managed execution service. The test container itself never receives the socket.

## Register and assess an application

1. Sign in to the dashboard and select **Register application**.
2. Enter the application name, a `https://github.com/owner/repository` URL, the branch to inspect, and its authorized staging URL. Technology stack and description are optional.
3. Select **Register application**. The registration API validates the repository URL and rejects staging addresses in private, loopback, link-local, or reserved networks.
4. Open **Applications** and select **Run QA preflight**.
5. View the asynchronous run results on the application card.

The read-only assessment checks GitHub access, recursively inspects the selected branch's file tree, detects repository languages and known test framework configuration files, then sends a single HTTP `GET` request to the registered staging URL. It does not change the GitHub repository or submit forms to the staging site. Authorized team members can retrieve a specific text file through `GET /api/applications/{id}/repository-file/?path=relative/path`, limited to 256 KiB per file. This endpoint requires the registration's explicit source-read authorization. File retrieval is read-only and does not execute code.

**Important scope:** repository source retrieval is available only for registrations that explicitly authorize source reads. The preflight does not clone a repository or execute the repository's existing test suite. Only individually generated, reviewed, and approved Python pytest candidates can be executed in the isolated runner. JavaScript, arbitrary project dependency installation, integration, browser, Locust, and ZAP test execution are not enabled by this workflow.

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
- `POST /api/applications/{id}/ai/test-suggestions/` — propose human-reviewed tests for an explicitly authorized source file; body: `{"path":"src/module.py"}`.
- `POST /api/applications/{id}/ai/provider-check/` — make a minimal live provider request to verify the configured OpenAI model.
- `POST /api/applications/{id}/ai/generate-test/` — generate and persist an unapproved pytest candidate for an authorized Python source file; body: `{"path":"src/module.py"}`.
- `GET /api/applications/{id}/ai/candidates/` — list persisted generated candidates for review.
- `POST /api/applications/{id}/ai/candidates/{candidate_id}/approve/` — explicitly approve one candidate.
- `POST /api/applications/{id}/ai/candidates/{candidate_id}/run/` — queue execution of an approved candidate in the isolated Docker sandbox.
- `POST /api/applications/{id}/ai/failure-analysis/` — analyze user-provided failure evidence; body: `{"test_name":"...","error_message":"...","stack_trace":"..."}`.
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
- `workers/runner/` — isolated Docker orchestrator and ephemeral Python pytest sandbox for explicitly approved AI candidate tests.
- `workers/python/`, `workers/node/`, and `workers/playwright/` — additional runner image templates that are not connected to this candidate workflow.
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
