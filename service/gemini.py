"""Gemini Developer API transport. Document evidence is validated by its callers."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from service.ollama import InferenceError, NoRedirect

MODEL = "gemini-3.1-flash-lite"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def model_name():
    return MODEL


def api_key():
    value = os.getenv("GEMINI_API_KEY", "").strip()
    if not value:
        try:
            with Path(".suriq/gemini-api-key").open(encoding="ascii") as secret:
                value = secret.read(4097).strip()
        except (OSError, UnicodeError):
            return ""
    return value if 0 < len(value) <= 4096 and all(char.isascii() and not char.isspace() for char in value) else ""


def require_key():
    value = api_key()
    if not value:
        raise InferenceError("Add a Gemini API key to enable hosted AI. Native extraction is still available.")
    return value


def timeout_seconds():
    return float(os.getenv("SURIQ_GEMINI_TIMEOUT", "30"))


def send(request, timeout):
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        return json.loads(response.read(1_000_001))


def response_schema(value):
    """Leave nested bounds to the local validator, where they cannot expand Gemini's grammar."""
    if isinstance(value, dict):
        return {key: response_schema(item) for key, item in value.items()
                if key not in {"minItems", "maxItems", "minLength", "maxLength"}}
    if isinstance(value, list):
        return [response_schema(item) for item in value]
    return value


def chat(model, schema, instructions, payload, *, images=(), tokens=2048, retries=0):
    if model != MODEL:
        raise InferenceError("Gemini is configured for Flash-Lite 3.1.")
    parts = [{"text": json.dumps(payload, ensure_ascii=False)}]
    parts.extend({"inlineData": {"mimeType": "image/png", "data": image}} for image in images)
    request = Request(ENDPOINT, data=json.dumps({
        "systemInstruction": {"parts": [{"text": instructions}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": response_schema(schema.model_json_schema()),
            "maxOutputTokens": tokens,
            "temperature": 0,
            "thinkingConfig": {"thinkingLevel": "MINIMAL"},
        },
    }).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": require_key()})
    for attempt in range(retries + 1):
        try:
            result = send(request, timeout_seconds())
            candidate = result["candidates"][0]
            if candidate.get("finishReason") != "STOP":
                raise ValueError("Incomplete generation")
            content = "".join(part["text"] for part in candidate["content"]["parts"]
                              if "text" in part and not part.get("thought"))
            return schema.model_validate_json(content)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status in {401, 403}:
                raise InferenceError("Gemini denied access. Check the API key and its project permissions.") from exc
            if status == 429:
                raise InferenceError("Gemini quota or rate limit reached. Try again later or review the source manually.") from exc
            if status < 500 or attempt == retries:
                raise InferenceError(f"Gemini request failed (HTTP {status}). Review the source or retry.") from exc
        except (URLError, OSError) as exc:
            if attempt == retries:
                raise InferenceError("Gemini is unreachable or timed out. Review the source or retry.") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise InferenceError("Gemini returned invalid or incomplete structured output. Review the source.") from exc
