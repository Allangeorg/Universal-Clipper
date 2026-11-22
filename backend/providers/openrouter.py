from __future__ import annotations

import json
import re
from typing import Any

import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"


class ProviderError(RuntimeError):
    pass


def is_nvidia_key(api_key: str) -> bool:
    return bool(api_key and api_key.strip().startswith("nvapi-"))


def is_openai_key(api_key: str) -> bool:
    cleaned = (api_key or "").strip()
    return cleaned.startswith("sk-") and not cleaned.startswith("sk-or-")


def resolve_best_model(api_key: str, kind: str = "llm") -> str:
    cleaned = (api_key or "").strip()
    if is_nvidia_key(cleaned):
        if kind == "vision":
            return "nvidia/nemotron-3.5-super-vl-120b-a12b"
        return "nvidia/nemotron-3-ultra-550b-a55b"
    if is_openai_key(cleaned):
        return "gpt-4o-mini"
    # Default OpenRouter
    if kind == "vision":
        return "meta-llama/llama-3.2-11b-vision-instruct"
    return "openrouter/auto"


def _base_url(api_key: str) -> str:
    return NVIDIA_BASE if is_nvidia_key(api_key) else OPENROUTER_BASE


def _headers(api_key: str) -> dict[str, str]:
    cleaned = (api_key or "").strip()
    if is_nvidia_key(cleaned):
        return {
            "Authorization": f"Bearer {cleaned}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    return {
        "Authorization": f"Bearer {cleaned}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://127.0.0.1:8765",
        "X-Title": "Universal Clipper",
    }


def list_models(api_key: str, vision_only: bool = False) -> list[dict[str, Any]]:
    base = _base_url(api_key)
    provider_name = "NVIDIA NIM" if is_nvidia_key(api_key) else "OpenRouter"
    try:
        response = httpx.get(f"{base}/models", headers=_headers(api_key), timeout=35)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        raise ProviderError(f"{provider_name} model-list request failed ({exc.response.status_code}): {detail}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"Could not reach {provider_name}: {exc}") from exc

    models = []
    if is_nvidia_key(api_key):
        for item in payload.get("data", []):
            mid = item.get("id", "")
            if not mid:
                continue
            is_vision = any(k in mid.lower() for k in ("vl", "vision", "video", "multimodal", "cosmos", "omni"))
            if vision_only and not is_vision:
                continue
            models.append({
                "id": mid,
                "name": f"{mid} (NVIDIA NIM)",
                "vision": is_vision,
                "context_length": item.get("context_length") or 32768,
                "pricing": {},
            })
    else:
        for item in payload.get("data", []):
            architecture = item.get("architecture") or {}
            modalities = architecture.get("input_modalities") or []
            if vision_only and "image" not in modalities:
                continue
            models.append({
                "id": item.get("id", ""),
                "name": item.get("name") or item.get("id", ""),
                "vision": "image" in modalities,
                "context_length": item.get("context_length"),
                "pricing": item.get("pricing") or {},
            })

    return sorted((m for m in models if m["id"]), key=lambda m: (not m["vision"], m["name"].lower()))



def list_transcription_models(api_key: str) -> list[dict[str, Any]]:
    """List current speech-to-text models."""
    if is_nvidia_key(api_key):
        return [
            {"id": "nvidia/nemotron-3.5-super-vl-120b-a12b", "name": "NVIDIA Nemotron 3.5 Super VL (Multimodal)",
             "description": "NVIDIA multimodal reasoning", "pricing": {}},
            {"id": "nvidia/riva-translate-4b-instruct-v2", "name": "NVIDIA Riva Audio/Speech (NIM)",
             "description": "NVIDIA speech audio model", "pricing": {}},
        ]
    base = _base_url(api_key)
    try:
        response = httpx.get(f"{base}/models", headers=_headers(api_key),
                             params={"output_modalities": "transcription"}, timeout=35)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        raise ProviderError(f"OpenRouter transcription model request failed ({exc.response.status_code}): {detail}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"Could not load OpenRouter transcription models: {exc}") from exc
    models = []
    for item in payload.get("data", []):
        model_id = item.get("id", "")
        if model_id:
            models.append({"id": model_id, "name": item.get("name") or model_id,
                           "description": item.get("description", ""), "pricing": item.get("pricing") or {}})
    return sorted(models, key=lambda item: ("whisper-large-v3-turbo" not in item["id"], item["name"].lower()))


def test_connection(api_key: str) -> dict[str, Any]:
    models = list_models(api_key)
    provider_name = "NVIDIA NIM" if is_nvidia_key(api_key) else "OpenRouter"
    return {"ok": True, "provider": provider_name, "message": f"Connected to {provider_name}. {len(models)} models are available."}


def chat(api_key: str, model: str, messages: list[dict[str, Any]], timeout: float = 120, max_tokens: int = 2400) -> str:
    base = _base_url(api_key)
    provider_name = "NVIDIA NIM" if is_nvidia_key(api_key) else "OpenRouter"

    # Detect if payload contains image URLs
    has_images = any(
        isinstance(msg.get("content"), list) and any(
            isinstance(part, dict) and part.get("type") == "image_url" for part in msg["content"]
        )
        for msg in messages
    )

    effective_model = (model or "").strip()
    if not effective_model or effective_model in {"auto", "vision-auto"} or (effective_model.startswith("openrouter/") and is_nvidia_key(api_key)):
        effective_model = resolve_best_model(api_key, kind="vision" if has_images else "llm")

    if not effective_model:
        raise ProviderError(f"Choose a {provider_name} model in Settings first.")

    body: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "temperature": 0.15,
        "max_tokens": max(256, min(8192, int(max_tokens))),
    }
    try:
        response = httpx.post(f"{base}/chat/completions", headers=_headers(api_key), json=body, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        choice = payload.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content")
        if not content:
            content = msg.get("reasoning_content") or ""
        if isinstance(content, list):
            content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"{provider_name} returned an empty response.")
        return content.strip()
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        raise ProviderError(f"{provider_name} request failed ({exc.response.status_code}): {detail}") from exc
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        if isinstance(exc, ProviderError):
            raise
        raise ProviderError(f"{provider_name} request could not be completed: {exc}") from exc



def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError("AI model did not return a valid JSON object.")
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError("AI model returned malformed JSON.") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("AI model response must be a JSON object.")
    return parsed


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", payload)
        if isinstance(error, dict):
            return str(error.get("message") or error)[:800]
        return str(error)[:800]
    except ValueError:
        return (response.text or "Unexpected provider error")[:800]
