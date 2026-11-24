import time

import httpx
from django.core.exceptions import ValidationError

from .validators import validate_external_url


def probe_staging_url(value: str) -> dict:
    try:
        validate_external_url(value)
    except ValidationError as exc:
        return {"reachable": False, "status_code": None, "latency_ms": None, "error": exc.messages[0]}

    started = time.monotonic()
    try:
        with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", value, headers={"User-Agent": "ZimworxQA/1.0"}) as response:
                return {
                    "reachable": 200 <= response.status_code < 400,
                    "status_code": response.status_code,
                    "latency_ms": round((time.monotonic() - started) * 1000, 2),
                    "error": "",
                }
    except httpx.TimeoutException:
        return {
            "reachable": False,
            "status_code": None,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "error": "Staging request timed out.",
        }
    except httpx.RequestError as exc:
        return {
            "reachable": False,
            "status_code": None,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "error": f"Staging request failed ({type(exc).__name__}).",
        }
