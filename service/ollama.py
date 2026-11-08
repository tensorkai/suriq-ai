"""Bounded local inference transport shared by intake and matching."""
import json
import os
import re
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class InferenceError(ValueError):
    pass


def enabled():
    return os.getenv("SURIQ_ENABLE_OLLAMA") == "1"


def model_name():
    return os.getenv("SURIQ_OLLAMA_MODEL", "qwen3-vl:4b-instruct")


def request_json(path, body=None, *, timeout=5):
    port = int(os.getenv("SURIQ_OLLAMA_PORT", "11434"))
    request = Request(f"http://127.0.0.1:{port}{path}",
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        return json.loads(response.read(1_000_001))


def installed_model(model):
    """Resolve a configured tag against installed weights before sending any document."""
    if not re.fullmatch(r"[\w./-]+(?::[\w.-]+)?", model) or "cloud" in model.lower():
        raise InferenceError("Configure an installed local Ollama model.")
    try:
        tag = model if ":" in model else f"{model}:latest"
        for entry in request_json("/api/tags")["models"]:
            if entry["name"] == tag and entry.get("digest") and not entry.get("remote_host"):
                return entry["digest"]
    except (URLError, OSError, ValueError, TypeError, KeyError) as exc:
        raise InferenceError("Local Ollama is unavailable. Start it and retry.") from exc
    raise InferenceError("The configured model is not installed locally.")


def timeout_seconds():
    return float(os.getenv("SURIQ_OLLAMA_TIMEOUT", "180"))


def chat(model, schema, instructions, payload, *, images=(), tokens=2048, retries=0):
    message = {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    if images:
        message["images"] = list(images)
    request = {
        "model": model, "stream": False, "think": False, "format": schema.model_json_schema(),
        "options": {"temperature": 0, "num_predict": tokens},
        "messages": [{"role": "system", "content": instructions}, message],
    }
    for attempt in range(retries + 1):
        try:
            result = request_json("/api/chat", request, timeout=timeout_seconds())
            return schema.model_validate_json(result["message"]["content"])
        except (URLError, OSError) as exc:
            if attempt == retries:
                raise InferenceError("Local inference failed or timed out. Retry or review the source.") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise InferenceError("Local model returned invalid structured output. Review the source.") from exc
