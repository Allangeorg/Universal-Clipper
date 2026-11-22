from __future__ import annotations

import os
from typing import Final

from backend.config import ENV_GROQ_API_KEY, ENV_OPENAI_API_KEY, ENV_OPENROUTER_API_KEY

SERVICE: Final = "UniversalClipper"
PROVIDERS = {"openrouter", "groq", "openai"}
ENV_KEYS = {
    "openrouter": ENV_OPENROUTER_API_KEY,
    "groq": ENV_GROQ_API_KEY,
    "openai": ENV_OPENAI_API_KEY,
}


class SecretStoreError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise SecretStoreError("Secure credential storage is unavailable. Install the keyring package and restart Universal Clipper.") from exc
    try:
        backend = keyring.get_keyring()
        if getattr(backend, "priority", 0) <= 0:
            raise SecretStoreError("No operating-system credential vault is available. Configure Windows Credential Manager, macOS Keychain, or a Secret Service keyring.")
        return keyring
    except SecretStoreError:
        raise
    except Exception as exc:
        raise SecretStoreError(f"Could not access the operating-system credential vault: {exc}") from exc


def get_secret(provider: str) -> str | None:
    if provider not in PROVIDERS:
        raise SecretStoreError("Unsupported credential provider.")
    # Explicit environment configuration takes precedence, without copying it
    # into application settings or returning it to the browser.
    value = ENV_KEYS.get(provider, "")
    if value:
        return value
    keyring = _keyring()
    try:
        return keyring.get_password(SERVICE, provider)
    except Exception as exc:
        raise SecretStoreError(f"Could not read the {provider} credential from the OS vault: {exc}") from exc


def has_secret(provider: str) -> bool:
    try:
        return bool(get_secret(provider))
    except SecretStoreError:
        return False


def set_secret(provider: str, value: str) -> None:
    if provider not in PROVIDERS:
        raise SecretStoreError("Unsupported credential provider.")
    cleaned = value.strip()
    if not cleaned:
        raise SecretStoreError("API key cannot be empty.")
    if len(cleaned) > 4096 or any(char in cleaned for char in "\r\n\x00"):
        raise SecretStoreError("API key format is invalid.")
    keyring = _keyring()
    try:
        keyring.set_password(SERVICE, provider, cleaned)
    except Exception as exc:
        raise SecretStoreError(f"Could not save the {provider} key in the OS credential vault: {exc}") from exc


def delete_secret(provider: str) -> None:
    if provider not in PROVIDERS:
        raise SecretStoreError("Unsupported credential provider.")
    if ENV_KEYS.get(provider):
        raise SecretStoreError(f"The {provider} key is configured through an environment variable and cannot be deleted from this screen.")
    keyring = _keyring()
    try:
        keyring.delete_password(SERVICE, provider)
    except keyring.errors.PasswordDeleteError:
        return
    except Exception as exc:
        raise SecretStoreError(f"Could not delete the {provider} key from the OS credential vault: {exc}") from exc
