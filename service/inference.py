"""Select the configured inference provider."""
import os

from service import gemini, meta, ollama

HOSTED = {"gemini": gemini, "meta": meta}


def provider():
    return os.getenv("SURIQ_AI_PROVIDER") or ("ollama" if ollama.enabled() else "gemini" if gemini.api_key()
                                              else "meta" if meta.api_key() else "off")


def hosted():
    return provider() in HOSTED


def public_status():
    selected = provider()
    return {"provider": selected, "configured": bool(HOSTED[selected].api_key()) if selected in HOSTED else selected == "ollama"}


def backend():
    selected = provider()
    if selected in HOSTED:
        return HOSTED[selected]
    if selected == "ollama":
        return ollama
    return None


def prepare(backend, model):
    if backend is ollama:
        return ollama.installed_model(model)
    backend.require_key()
    return None
