from celery import shared_task


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def execute_discovery(self, discovery_run_id):
    # Phase 2 replaces this bounded task with the OpenAPI/HTTPX discovery engine.
    return {"discovery_run_id": str(discovery_run_id), "status": "queued_for_phase_2"}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def execute_test_run(self, test_run_id):
    # Phase 2 replaces this orchestration stub with standardized engine adapters.
    return {"test_run_id": str(test_run_id), "status": "queued_for_phase_2"}
