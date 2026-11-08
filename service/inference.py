"""Select the configured inference provider and enforce hosted opt-in."""
import os

from service import gemini, ollama


def provider():
    return os.getenv("SURIQ_AI_PROVIDER") or ("ollama" if ollama.enabled() else "off")


def public_status():
    selected = provider()
    return {"provider": selected, "configured": bool(gemini.api_key()) if selected == "gemini" else selected == "ollama"}


def backend(use_gemini):
    selected = provider()
    if selected == "gemini" and use_gemini:
        return gemini
    if selected == "ollama":
        return ollama
    return None


def prepare(backend, model):
    if backend is ollama:
        return ollama.installed_model(model)
    gemini.require_key()
    return None
