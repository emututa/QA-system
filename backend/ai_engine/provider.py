import json
import os
import re

import httpx


class AIConfigurationError(Exception):
    pass


class AIProviderError(Exception):
    def __init__(self, status_code: int | None):
        self.status_code = status_code
        if status_code == 401:
            message = "OpenAI rejected the configured API key."
        elif status_code == 403:
            message = "The configured OpenAI key is not permitted to use this model."
        elif status_code == 429:
            message = "OpenAI rate limit or usage quota was reached."
        elif status_code is not None and status_code >= 500:
            message = "OpenAI is temporarily unavailable."
        else:
            message = "OpenAI request failed."
        super().__init__(message)


SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^(\s*(?:(?:export\s+)?(?:const|let|var)\s+)?"
    r"(?:[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CLIENT[_-]?SECRET|PRIVATE[_-]?KEY)[A-Z0-9_]*|authorization)"
    r"\s*[:=]\s*).+$"
)
BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
OPENAI_KEY_PATTERN = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")
GITHUB_TOKEN_PATTERN = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}")


def redact_secrets(value: str) -> str:
    redacted = SECRET_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", value)
    redacted = BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    redacted = OPENAI_KEY_PATTERN.sub("[REDACTED]", redacted)
    return GITHUB_TOKEN_PATTERN.sub("[REDACTED]", redacted)


def validate_ai_configuration() -> tuple[str, str]:
    if os.getenv("AI_ENABLED", "false").strip().lower() != "true":
        raise AIConfigurationError("AI features are disabled. Set AI_ENABLED=true to enable them.")
    if os.getenv("AI_PROVIDER", "openai").strip().lower() != "openai":
        raise AIConfigurationError("Only the OpenAI provider is currently supported.")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()
    if not api_key or not model:
        raise AIConfigurationError("Configure OPENAI_API_KEY and OPENAI_MODEL in the server environment.")
    return api_key, model


def request_json(*, instructions: str, prompt: str) -> dict:
    api_key, model = validate_ai_configuration()
    payload = {
        "model": model,
        "instructions": instructions,
        "input": redact_secrets(prompt),
        "store": False,
        "text": {"format": {"type": "json_object"}},
    }
    try:
        with httpx.Client(timeout=45, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.RequestError as exc:
        raise AIProviderError(None) from exc

    if response.status_code != 200:
        raise AIProviderError(response.status_code)

    try:
        response_data = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise AIProviderError(None) from exc
    if not isinstance(response_data, dict):
        raise AIProviderError(None)
    output_text = response_data.get("output_text")
    if not isinstance(output_text, str):
        outputs = response_data.get("output", [])
        if not isinstance(outputs, list):
            raise AIProviderError(None)
        output_text = "\n".join(
            item.get("text", "")
            for output in outputs
            if isinstance(output, dict)
            for item in output.get("content", [])
            if isinstance(item, dict) and item.get("type") == "output_text"
        )
    try:
        parsed = json.loads(output_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AIProviderError(None) from exc
    if not isinstance(parsed, dict):
        raise AIProviderError(None)
    return parsed
