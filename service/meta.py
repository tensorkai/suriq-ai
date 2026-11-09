"""Meta Model API transport (OpenAI-compatible chat completions). Document evidence is validated by its callers."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from service.gemini import response_schema
from service.ollama import InferenceError, NoRedirect

ENDPOINT = "https://api.meta.ai/v1/chat/completions"
# Reasoning tokens count against max_completion_tokens, so callers' output budgets get this headroom.
REASONING_HEADROOM = {"low": 4096, "medium": 8192, "high": 24576}


def model_name():
    return os.getenv("SURIQ_META_MODEL", "muse-spark-1.3-contributor")


def reasoning_effort():
    return os.getenv("SURIQ_META_REASONING", "medium")


def api_key():
    value = os.getenv("MODEL_API_KEY", "").strip()
    return value if 0 < len(value) <= 4096 and all(char.isascii() and not char.isspace() for char in value) else ""


def require_key():
    value = api_key()
    if not value:
        raise InferenceError("Add a Meta MODEL_API_KEY to enable hosted AI. Native extraction is still available.")
    return value


def timeout_seconds():
    return float(os.getenv("SURIQ_META_TIMEOUT", "120"))


def send(request, timeout):
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        return json.loads(response.read(1_000_001))


def chat(model, schema, instructions, payload, *, images=(), tokens=2048, retries=0, temperature=0):
    content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    content.extend({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}} for image in images)
    request = Request(ENDPOINT, data=json.dumps({
        "model": model,
        "reasoning_effort": reasoning_effort(),
        "temperature": temperature,
        "max_completion_tokens": tokens + REASONING_HEADROOM.get(reasoning_effort(), 8192),
        "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": False, "schema": response_schema(schema.model_json_schema())}},
    }).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {require_key()}"})
    for attempt in range(retries + 1):
        try:
            choice = send(request, timeout_seconds())["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("Incomplete generation")
            return schema.model_validate_json(choice["message"]["content"])
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status in {401, 403}:
                raise InferenceError("Meta denied access. Check the MODEL_API_KEY.") from exc
            if status == 429:
                raise InferenceError("Meta quota or rate limit reached. Try again later or review the source manually.") from exc
            if status < 500 or attempt == retries:
                raise InferenceError(f"Meta request failed (HTTP {status}). Review the source or retry.") from exc
        except (URLError, OSError) as exc:
            if attempt == retries:
                raise InferenceError("Meta is unreachable or timed out. Review the source or retry.") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise InferenceError("Meta returned invalid or incomplete structured output. Review the source.") from exc
