"""Validación de la configuración del proveedor de IA.

Este módulo no depende de FastAPI ni llama a ningún proveedor. Mantiene la
validación, el límite de seguridad y la representación pública de la
configuración separados del router HTTP para poder probarlos de forma aislada.
La credencial se carga desde una variable codificada en base64 y nunca forma
parte del estado público.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import os
import re
from typing import Mapping
from urllib.parse import urlsplit


OPENAI_BASE_URL = "https://api.openai.com/v1"
PROVIDERS = {"openai", "openai_compatible"}
MAX_API_KEY_CHARS = 8192

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ENV_SAFE_URL_RE = re.compile(r"^[A-Za-z0-9:/._~%+\[\]-]+$")
_TRUE_VALUES = {"1", "true", "yes", "on"}


class AiSettingsError(ValueError):
    """Indica que la configuración propuesta no es admisible."""


def security_state(env: Mapping[str, str] | None = None) -> dict:
    """Describe si la función puede operar con el transporte actual."""
    values = os.environ if env is None else env
    auth = bool(str(values.get("MODULINKR_WEB_USER", "")).strip()
                and str(values.get("MODULINKR_WEB_PASS", "")))
    tls = bool(str(values.get("MODULINKR_WEB_CERT", "")).strip()
               and str(values.get("MODULINKR_WEB_KEY", "")).strip())
    development = str(values.get(
        "MODULINKR_AI_ALLOW_INSECURE_DEV", "")).strip().lower() in _TRUE_VALUES

    if auth and tls:
        return {
            "security_ready": True,
            "security_mode": "protected",
            "blocked_reason": None,
        }
    if development:
        return {
            "security_ready": True,
            "security_mode": "development",
            "blocked_reason": None,
        }

    missing = []
    if not auth:
        missing.append("autenticación")
    if not tls:
        missing.append("HTTPS")
    return {
        "security_ready": False,
        "security_mode": "blocked",
        "blocked_reason": (
            "El asistente requiere " + " y ".join(missing)
            + ". Reejecuta el instalador del visor antes de configurarlo."
        ),
    }


def _validate_model(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise AiSettingsError("el modelo debe ser texto")
    model = value.strip()
    if allow_empty and not model:
        return ""
    if not _MODEL_RE.fullmatch(model):
        raise AiSettingsError(
            "el modelo debe tener entre 1 y 128 caracteres y usar solo "
            "letras, números, punto, guion, barra, dos puntos o guion bajo"
        )
    return model


def _validate_base_url(value: object, *, allow_local_http: bool = False) -> str:
    if not isinstance(value, str):
        raise AiSettingsError("la URL base debe ser texto")
    base_url = value.strip().rstrip("/")
    if not base_url or len(base_url) > 2048:
        raise AiSettingsError("la URL base es obligatoria y admite 2048 caracteres")
    if not _ENV_SAFE_URL_RE.fullmatch(base_url):
        raise AiSettingsError(
            "la URL base contiene caracteres no admitidos o parámetros de consulta"
        )

    parsed = urlsplit(base_url)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise AiSettingsError(
            "la URL base no admite credenciales, consulta ni fragmento"
        )
    if not parsed.hostname:
        raise AiSettingsError("la URL base no contiene un servidor válido")
    try:
        parsed.port
    except ValueError as exc:
        raise AiSettingsError("la URL base contiene un puerto inválido") from exc

    hostname = parsed.hostname.lower().rstrip(".")
    local_host = hostname == "localhost"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    local_loopback = bool(address and address.is_loopback)

    if parsed.scheme == "http":
        if not (allow_local_http and (local_host or local_loopback)):
            raise AiSettingsError("la URL base debe usar HTTPS")
    elif parsed.scheme != "https":
        raise AiSettingsError("la URL base debe usar HTTPS")

    if local_host or hostname.endswith((".local", ".lan", ".internal")):
        if not (allow_local_http and local_host):
            raise AiSettingsError("la URL base no puede apuntar a una red local")
    if address and not address.is_global:
        if not (allow_local_http and address.is_loopback):
            raise AiSettingsError("la URL base no puede apuntar a una dirección no pública")
    return base_url


def _validate_api_key(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AiSettingsError("la clave API debe ser texto")
    if not value:
        return ""
    if not (8 <= len(value) <= MAX_API_KEY_CHARS):
        raise AiSettingsError("la clave API debe tener entre 8 y 8192 caracteres")
    if not value.isascii():
        raise AiSettingsError("la clave API debe usar caracteres ASCII")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127
           for char in value):
        raise AiSettingsError("la clave API no puede contener espacios ni controles")
    return value


def validate_config(body: object, *, allow_local_http: bool = False) -> dict:
    """Valida un cuerpo del formulario y devuelve una copia normalizada."""
    if not isinstance(body, dict):
        raise AiSettingsError("se esperaba un objeto JSON")
    allowed = {"provider", "model", "base_url", "api_key"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise AiSettingsError("campo no admitido: " + unknown[0])
    missing = sorted({"provider", "model", "base_url"} - set(body))
    if missing:
        raise AiSettingsError("campo obligatorio ausente: " + missing[0])

    provider = body["provider"]
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise AiSettingsError("proveedor no admitido")
    model = _validate_model(body["model"])

    if provider == "openai":
        supplied_url = body["base_url"].strip() if isinstance(
            body["base_url"], str) else ""
        if supplied_url.rstrip("/") != OPENAI_BASE_URL:
            raise AiSettingsError("la URL base de OpenAI es fija")
        base_url = OPENAI_BASE_URL
    else:
        base_url = _validate_base_url(
            body["base_url"], allow_local_http=allow_local_http)

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": _validate_api_key(body.get("api_key", "")),
    }


def encode_api_key(api_key: str) -> str:
    """Codifica el secreto para persistirlo sin sintaxis activa de shell."""
    return base64.b64encode(api_key.encode("utf-8")).decode("ascii")


def decode_api_key(encoded: str) -> str:
    """Recupera la credencial del entorno; un valor corrupto queda vacío."""
    if not encoded:
        return ""
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = raw.decode("utf-8")
        return _validate_api_key(value)
    except (AiSettingsError, binascii.Error, UnicodeDecodeError, ValueError):
        return ""


def verification_fingerprint(config: Mapping[str, str], api_key: str) -> str:
    """Identifica la combinación exacta comprobada sin persistir la clave."""
    if not api_key:
        return ""
    parts = (
        str(config.get("provider", "")),
        str(config.get("model", "")),
        str(config.get("base_url", "")),
        api_key,
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def verification_matches(config: Mapping[str, str], api_key: str,
                         fingerprint: str) -> bool:
    """Confirma que el resultado guardado pertenece a la configuración vigente."""
    expected = verification_fingerprint(config, api_key)
    return bool(expected and fingerprint
                and hmac.compare_digest(expected, fingerprint))


def load_runtime_config(env: Mapping[str, str] | None = None) -> tuple[dict, str]:
    """Carga valores de entorno sin propagar entradas inválidas al router."""
    values = os.environ if env is None else env
    provider = str(values.get("MODULINKR_AI_PROVIDER", "openai"))
    if provider not in PROVIDERS:
        provider = "openai"
    try:
        model = _validate_model(
            str(values.get("MODULINKR_AI_MODEL", "")), allow_empty=True)
    except AiSettingsError:
        model = ""

    if provider == "openai":
        base_url = OPENAI_BASE_URL
    else:
        try:
            base_url = _validate_base_url(
                str(values.get("MODULINKR_AI_BASE_URL", "")),
                allow_local_http=(security_state(values)["security_mode"]
                                  == "development"),
            )
        except AiSettingsError:
            base_url = ""
    api_key = decode_api_key(str(values.get(
        "MODULINKR_AI_API_KEY_B64", "")))
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
    }, api_key


def public_state(config: Mapping[str, str], api_key: str,
                 security: Mapping[str, object],
                 *, connection_tested: bool = False) -> dict:
    """Estado entregable al navegador, sin el valor de la credencial."""
    public_config = {
        "provider": config.get("provider", "openai"),
        "model": config.get("model", ""),
        "base_url": config.get("base_url", OPENAI_BASE_URL),
    }
    configured = bool(public_config["model"] and public_config["base_url"])
    return {
        "config": public_config,
        "credential_configured": bool(api_key),
        "provider_configured": configured,
        "configuration_complete": configured and bool(api_key),
        "security_ready": bool(security.get("security_ready")),
        "security_mode": security.get("security_mode", "blocked"),
        "blocked_reason": security.get("blocked_reason"),
        "connection_tested": bool(connection_tested),
    }
