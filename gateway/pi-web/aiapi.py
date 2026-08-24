"""API del proveedor de IA y del asistente Modbus.

La credencial entra por HTTPS, se pasa al script privilegiado por stdin y se
persiste codificada en el archivo operativo de solo root. El endpoint de
estado devuelve únicamente si existe. Las propuestas Modbus se solicitan al
proveedor con salida estructurada y se vuelven a validar antes de devolverlas
al navegador.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ai_settings import (
    AiSettingsError,
    encode_api_key,
    load_runtime_config,
    public_state,
    security_state,
    validate_config,
    verification_fingerprint,
    verification_matches,
)
from modbus_ai_contract import (
    MAX_PROPOSAL_BYTES,
    ProposalValidationError,
    application_errors,
    validate_proposal,
)
from modbus_ai_provider import (
    AssistantRequestError,
    ProviderCallError,
    request_proposal,
    test_provider_connection,
    validate_assistant_request,
)


LOG = logging.getLogger("modulinkr.web.ai")
SERVICE_DIR = Path(__file__).resolve().parent.parent / "pi-service"
SET_AI_SH = SERVICE_DIR / "set_ai.sh"
MAX_BODY_BYTES = 16 * 1024
MAX_ASSISTANT_BODY_BYTES = 15 * 1024 * 1024

router = APIRouter(prefix="/api/ia")

_CONFIG, _API_KEY = load_runtime_config()
_CONNECTION_TESTED = verification_matches(
    _CONFIG,
    _API_KEY,
    os.environ.get("MODULINKR_AI_VERIFIED_SHA256", ""),
)
_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _sudo_stdin(script: Path, stdin: str,
                timeout_s: float = 20) -> tuple[bool, str]:
    if not script.is_file():
        return False, f"{script.name} no está junto a pi-service"
    try:
        result = subprocess.run(
            ["sudo", "-n", str(script)], input=stdin,
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, "la operación excedió el tiempo máximo"
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 and "password is required" in output:
        return False, (
            "sudo sin regla para el visor: reejecutar el instalador "
            "(sudoers de modulinkr-web)"
        )
    return result.returncode == 0, output


def _state() -> dict:
    with _LOCK:
        config = dict(_CONFIG)
        api_key = _API_KEY
        connection_tested = _CONNECTION_TESTED
    return public_state(
        config, api_key, security_state(),
        connection_tested=connection_tested,
    )


async def _body(request: Request, maximum: int = MAX_BODY_BYTES
                ) -> tuple[object | None, JSONResponse | None]:
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > maximum:
            return None, _err(413, "la configuración supera el tamaño permitido")
    except ValueError:
        return None, _err(400, "Content-Length inválido")
    raw = await request.body()
    if len(raw) > maximum:
        return None, _err(413, "la configuración supera el tamaño permitido")
    try:
        return json.loads(raw or b"{}"), None
    except json.JSONDecodeError:
        return None, _err(400, "body JSON inválido")


@router.get("/estado")
def estado():
    return _state()


@router.post("/guardar")
async def guardar(request: Request):
    global _CONFIG, _API_KEY, _CONNECTION_TESTED

    security = security_state()
    if not security["security_ready"]:
        return _err(403, str(security["blocked_reason"]))

    body, error = await _body(request)
    if error:
        return error
    try:
        config = validate_config(
            body,
            allow_local_http=(security["security_mode"] == "development"),
        )
    except AiSettingsError as exc:
        return _err(400, str(exc))

    with _LOCK:
        saved_api_key = _API_KEY
    supplied_api_key = config["api_key"]
    effective_api_key = supplied_api_key or saved_api_key
    if not effective_api_key:
        return _err(
            400,
            "Introduce una clave API para comprobar y guardar la configuración.",
        )

    if not _INFERENCE_LOCK.acquire(blocking=False):
        return _err(409, "Ya hay una consulta al proveedor en curso.")

    def test_locked():
        try:
            test_provider_connection(
                config,
                effective_api_key,
                security_mode=str(security["security_mode"]),
            )
        finally:
            _INFERENCE_LOCK.release()

    try:
        await asyncio.to_thread(test_locked)
    except ProviderCallError as exc:
        LOG.warning(
            "comprobación de configuración IA falló: %s",
            exc.technical_detail or str(exc),
        )
        return _err(502, "No se guardó la configuración. " + str(exc))

    lines = [
        f"MODULINKR_AI_PROVIDER={config['provider']}",
        f"MODULINKR_AI_MODEL={config['model']}",
        f"MODULINKR_AI_BASE_URL={config['base_url']}",
        "MODULINKR_AI_VERIFIED_SHA256="
        + verification_fingerprint(config, effective_api_key),
    ]
    if supplied_api_key:
        lines.append(
            f"MODULINKR_AI_API_KEY_B64={encode_api_key(supplied_api_key)}")

    ok, output = _sudo_stdin(SET_AI_SH, "\n".join(lines) + "\n")
    if not ok:
        LOG.warning("guardar configuración IA falló: %s", output)
        return _err(502, output)

    public_config = {key: config[key]
                     for key in ("provider", "model", "base_url")}
    with _LOCK:
        _CONFIG = public_config
        if supplied_api_key:
            _API_KEY = supplied_api_key
        _CONNECTION_TESTED = True
    LOG.info("configuración IA comprobada y guardada (provider=%s model=%s)",
             config["provider"], config["model"])
    return {"ok": True, **_state()}


@router.post("/modbus/validar")
async def modbus_validar(request: Request):
    body, error = await _body(request, MAX_PROPOSAL_BYTES + 4096)
    if error:
        return error
    if not isinstance(body, dict) or set(body) != {"proposal"}:
        return _err(400, "se esperaba únicamente el campo proposal")
    try:
        proposal = validate_proposal(body["proposal"])
        errors = application_errors(proposal)
    except ProposalValidationError as exc:
        LOG.warning("validación local de propuesta Modbus IA falló: %s",
                    "; ".join(exc.errors[:8]))
        return _err(
            400,
            "La selección contiene datos que el formulario no puede representar de forma segura.",
        )
    return {
        "ok": True,
        "proposal": proposal,
        "ready": not errors,
        "application_errors": errors,
    }


@router.post("/modbus/proponer")
async def modbus_proponer(request: Request):
    security = security_state()
    if not security["security_ready"]:
        return _err(403, str(security["blocked_reason"]))

    with _LOCK:
        config = dict(_CONFIG)
        api_key = _API_KEY
    if not config.get("model") or not config.get("base_url"):
        return _err(409, "configura el proveedor y el modelo antes de usar el asistente")
    if not api_key:
        return _err(409, "configura la clave API antes de usar el asistente")

    body, error = await _body(request, MAX_ASSISTANT_BODY_BYTES)
    if error:
        return error
    try:
        assistant_request = validate_assistant_request(body)
    except AssistantRequestError as exc:
        return _err(400, str(exc))

    if not _INFERENCE_LOCK.acquire(blocking=False):
        return _err(409, "ya hay una consulta del asistente en curso")
    def run_locked():
        try:
            return request_proposal(
                config,
                api_key,
                assistant_request,
                security_mode=str(security["security_mode"]),
            )
        finally:
            _INFERENCE_LOCK.release()

    try:
        LOG.info("consulta Modbus IA iniciada (provider=%s model=%s web=%s)",
                 config.get("provider"), config.get("model"),
                 assistant_request["use_web"])
        result = await asyncio.to_thread(run_locked)
    except ProviderCallError as exc:
        LOG.warning(
            "consulta Modbus IA rechazada: %s",
            exc.technical_detail or str(exc),
        )
        return _err(502, str(exc))
    LOG.info("consulta Modbus IA completada (ready=%s pendientes=%d)",
             result["ready"], len(result["proposal"]["pending"]))
    return {"ok": True, **result}
