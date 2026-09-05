"""Contrato seguro para propuestas del asistente Modbus.

El modelo solo puede proponer datos. Este módulo valida su estructura y su
coherencia antes de que otra capa decida si los muestra o los copia al
formulario. No ejecuta instrucciones contenidas en manuales, resultados web
ni respuestas del modelo.
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Dict, List, Optional, Set


CONTRACT_VERSION = "1.2"
MAX_PROPOSAL_BYTES = 128 * 1024
MAX_PROPOSAL_READS = 32
MAX_PROPOSAL_WRITES = 16
MAX_PROPOSAL_PENDING = 64
MAX_PROPOSAL_UNSUPPORTED = 32

READ_FUNCTIONS = {
    "read_coils",
    "read_discrete_inputs",
    "read_holding_registers",
    "read_input_registers",
}
WRITE_FUNCTIONS = {
    "write_single_coil",
    "write_multiple_coils",
    "write_single_register",
    "write_multiple_registers",
}
BIT_FUNCTIONS = {
    "read_coils",
    "read_discrete_inputs",
    "write_single_coil",
    "write_multiple_coils",
}
SINGLE_WRITE_FUNCTIONS = {"write_single_coil", "write_single_register"}
VALUE_TYPES = {"uint16", "int16", "uint32", "int32", "float32"}
MULTI_REGISTER_TYPES = {"uint32", "int32", "float32"}
REGISTER_TYPE_COUNTS = {
    "uint16": 1,
    "int16": 1,
    "uint32": 2,
    "int32": 2,
    "float32": 2,
}
BYTE_ORDERS = {"ABCD", "BADC", "CDAB", "DCBA"}
BAUDRATES = {2400, 4800, 9600, 19200, 38400, 57600, 115200}
PARITIES = {"N", "E", "O"}
READ_MODES = {"grouped", "individual"}
CHANGE_FUNCTIONS = {"write_single_register", "write_single_coil"}
SOURCE_KINDS = {"manual", "web", "user"}
UNSUPPORTED_CATEGORIES = {
    "bus_conflict",
    "catalog_limit",
    "communication",
    "data_shape",
    "mask",
    "password",
    "unlock",
    "sequence",
    "timing",
    "verification",
    "other",
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,7}$")
_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_PENDING_FIELD_RE = re.compile(
    r"^(?:"
    r"identity\.(?:manufacturer|model|revision)|"
    r"bus\.(?:baudrate|parity|stopbits)|"
    r"device\.(?:name|description|default_slave_id|desired_slave_id|"
    r"change_function|change_address|read_mode|inter_read_ms)|"
    r"(?:reads|writes)\.[a-z][a-z0-9_]{1,7}\."
    r"(?:id|name|function|address|count|type|byte_order|scale|offset|unit)"
    r")$"
)


def _nullable(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


_EVIDENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_id", "page", "section", "excerpt"],
    "properties": {
        "source_id": {"type": "string", "pattern": _SOURCE_ID_RE.pattern},
        "page": _nullable({"type": "integer", "minimum": 1}),
        "section": _nullable({"type": "string", "minLength": 1,
                              "maxLength": 160}),
        "excerpt": {"type": "string", "minLength": 1, "maxLength": 800},
    },
}

_ENTRY_PROPERTIES = {
    "id": _nullable({"type": "string", "pattern": _ID_RE.pattern}),
    "name": _nullable({"type": "string", "minLength": 1, "maxLength": 32}),
    "function": {},
    "address": _nullable({"type": "integer", "minimum": 0,
                           "maximum": 65535}),
    "count": _nullable({"type": "integer", "minimum": 1, "maximum": 125}),
    "type": _nullable({"type": "string", "enum": sorted(VALUE_TYPES)}),
    "byte_order": _nullable({"type": "string", "enum": sorted(BYTE_ORDERS)}),
    "scale": _nullable({"type": "number"}),
    "offset": _nullable({"type": "number"}),
    "unit": _nullable({"type": "string", "maxLength": 8}),
    "evidence": {"type": "array", "maxItems": 16,
                 "items": {"$ref": "#/$defs/evidence"}},
}

_WRITE_ENTRY_PROPERTIES = {
    **_ENTRY_PROPERTIES,
    "purpose": {"type": "string", "enum": ["commissioning", "operational"]},
}


def _entry_schema(functions: Set[str], *, write: bool = False) -> Dict[str, Any]:
    entry_properties = _WRITE_ENTRY_PROPERTIES if write else _ENTRY_PROPERTIES
    properties = copy.deepcopy(entry_properties)
    properties["function"] = _nullable({"type": "string",
                                        "enum": sorted(functions)})
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(entry_properties),
        "properties": properties,
    }


# Se entrega al proveedor como salida estructurada. Las reglas que dependen de
# más de un campo se vuelven a comprobar con validate_proposal().
PROPOSAL_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ModuLinkr Modbus AI proposal",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "contract_version", "sources", "identity", "bus", "device",
        "reads", "writes", "pending", "unsupported",
    ],
    "properties": {
        "contract_version": {
            "type": "string",
            "enum": [CONTRACT_VERSION],
        },
        "sources": {
            "type": "array",
            "maxItems": 16,
            "items": {"$ref": "#/$defs/source"},
        },
        "identity": {"$ref": "#/$defs/identity"},
        "bus": {"$ref": "#/$defs/bus"},
        "device": {"$ref": "#/$defs/device"},
        "reads": {
            "type": "array", "maxItems": MAX_PROPOSAL_READS,
            "items": {"$ref": "#/$defs/read"},
        },
        "writes": {
            "type": "array", "maxItems": MAX_PROPOSAL_WRITES,
            "items": {"$ref": "#/$defs/write"},
        },
        "pending": {
            "type": "array", "maxItems": MAX_PROPOSAL_PENDING,
            "items": {"$ref": "#/$defs/pending"},
        },
        "unsupported": {
            "type": "array", "maxItems": MAX_PROPOSAL_UNSUPPORTED,
            "items": {"$ref": "#/$defs/unsupported"},
        },
    },
    "$defs": {
        "source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "kind", "title", "url"],
            "properties": {
                "id": {"type": "string", "pattern": _SOURCE_ID_RE.pattern},
                "kind": {"type": "string", "enum": sorted(SOURCE_KINDS)},
                "title": {"type": "string", "minLength": 1,
                          "maxLength": 160},
                "url": _nullable({"type": "string", "maxLength": 2048}),
            },
        },
        "evidence": _EVIDENCE_SCHEMA,
        "identity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["manufacturer", "model", "revision", "evidence"],
            "properties": {
                "manufacturer": _nullable({"type": "string", "minLength": 1,
                                             "maxLength": 80}),
                "model": _nullable({"type": "string", "minLength": 1,
                                      "maxLength": 80}),
                "revision": _nullable({"type": "string", "minLength": 1,
                                         "maxLength": 80}),
                "evidence": {"type": "array", "maxItems": 16,
                             "items": {"$ref": "#/$defs/evidence"}},
            },
        },
        "bus": {
            "type": "object",
            "additionalProperties": False,
            "required": ["baudrate", "parity", "stopbits", "evidence"],
            "properties": {
                "baudrate": _nullable({"type": "integer",
                                       "enum": sorted(BAUDRATES)}),
                "parity": _nullable({"type": "string",
                                     "enum": sorted(PARITIES)}),
                "stopbits": _nullable({"type": "integer", "enum": [1, 2]}),
                "evidence": {"type": "array", "maxItems": 16,
                             "items": {"$ref": "#/$defs/evidence"}},
            },
        },
        "device": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "name", "description", "default_slave_id", "desired_slave_id",
                "change_function", "change_address", "read_mode",
                "inter_read_ms", "evidence",
            ],
            "properties": {
                "name": _nullable({"type": "string", "minLength": 1,
                                   "maxLength": 16}),
                "description": _nullable({"type": "string", "minLength": 1,
                                          "maxLength": 256}),
                "default_slave_id": _nullable({"type": "integer", "minimum": 1,
                                               "maximum": 247}),
                "desired_slave_id": _nullable({"type": "integer", "minimum": 1,
                                               "maximum": 247}),
                "change_function": _nullable({"type": "string",
                                              "enum": sorted(CHANGE_FUNCTIONS)}),
                "change_address": _nullable({"type": "integer", "minimum": 0,
                                             "maximum": 65535}),
                "read_mode": _nullable({"type": "string",
                                        "enum": sorted(READ_MODES)}),
                "inter_read_ms": _nullable({"type": "integer", "minimum": 0,
                                            "maximum": 5000}),
                "evidence": {"type": "array", "maxItems": 16,
                             "items": {"$ref": "#/$defs/evidence"}},
            },
        },
        "read": _entry_schema(READ_FUNCTIONS),
        "write": _entry_schema(WRITE_FUNCTIONS, write=True),
        "pending": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "scope", "field", "question", "reason", "can_research_web",
                "web_query", "evidence",
            ],
            "properties": {
                "scope": {"type": "string",
                          "enum": ["bus", "device", "read", "write"]},
                "field": {"type": "string", "pattern": _PENDING_FIELD_RE.pattern,
                          "maxLength": 80},
                "question": {"type": "string", "minLength": 1,
                             "maxLength": 500},
                "reason": {"type": "string", "minLength": 1,
                           "maxLength": 500},
                "can_research_web": {"type": "boolean"},
                "web_query": _nullable({"type": "string", "minLength": 1,
                                        "maxLength": 300}),
                "evidence": {"type": "array", "maxItems": 16,
                             "items": {"$ref": "#/$defs/evidence"}},
            },
        },
        "unsupported": {
            "type": "object",
            "additionalProperties": False,
            "required": ["category", "summary", "reason", "evidence"],
            "properties": {
                "category": {"type": "string",
                             "enum": sorted(UNSUPPORTED_CATEGORIES)},
                "summary": {"type": "string", "minLength": 1,
                            "maxLength": 500},
                "reason": {"type": "string", "minLength": 1,
                           "maxLength": 500},
                "evidence": {"type": "array", "maxItems": 16,
                             "items": {"$ref": "#/$defs/evidence"}},
            },
        },
    },
}


class ProposalValidationError(ValueError):
    """Indica que una propuesta no cumple el contrato de confianza."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _object(value: Any, path: str, keys: Set[str],
            errors: List[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        errors.append(f"{path}: debe ser un objeto")
        return None
    missing = sorted(keys - set(value))
    unknown = sorted(set(value) - keys)
    for key in missing:
        errors.append(f"{path}.{key}: campo obligatorio ausente")
    for key in unknown:
        errors.append(f"{path}.{key}: campo no admitido")
    return value


def _array(value: Any, path: str, errors: List[str],
           maximum: int) -> Optional[List[Any]]:
    if not isinstance(value, list):
        errors.append(f"{path}: debe ser un array")
        return None
    if len(value) > maximum:
        errors.append(f"{path}: admite como máximo {maximum} elementos")
    return value


def _text(value: Any, path: str, errors: List[str], maximum: int,
          nullable: bool = False, minimum: int = 1,
          pattern: Optional[re.Pattern] = None) -> Optional[str]:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        errors.append(f"{path}: debe ser texto" + (" o null" if nullable else ""))
        return None
    if len(value) < minimum or len(value) > maximum:
        errors.append(f"{path}: longitud fuera de {minimum}-{maximum}")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        errors.append(f"{path}: contiene caracteres de control no admitidos")
    if pattern is not None and not pattern.fullmatch(value):
        errors.append(f"{path}: formato no admitido")
    return value


def _integer(value: Any, path: str, errors: List[str], minimum: int,
             maximum: int, nullable: bool = False) -> Optional[int]:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{path}: debe ser entero" + (" o null" if nullable else ""))
        return None
    if value < minimum or value > maximum:
        errors.append(f"{path}: debe estar entre {minimum} y {maximum}")
    return value


def _number(value: Any, path: str, errors: List[str],
            nullable: bool = False) -> Optional[float]:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path}: debe ser numérico" + (" o null" if nullable else ""))
        return None
    if not math.isfinite(value):
        errors.append(f"{path}: debe ser finito")
    return float(value)


def _choice(value: Any, path: str, errors: List[str], choices: Set[Any],
            nullable: bool = False) -> Any:
    if value is None and nullable:
        return None
    if value not in choices or isinstance(value, bool):
        errors.append(f"{path}: valor no admitido")
        return None
    return value


def _evidence(value: Any, path: str, errors: List[str],
              source_ids: Set[str], minimum: int = 0) -> None:
    items = _array(value, path, errors, 16)
    if items is None:
        return
    if len(items) < minimum:
        errors.append(f"{path}: requiere al menos {minimum} referencia")
    keys = {"source_id", "page", "section", "excerpt"}
    for index, raw in enumerate(items):
        item_path = f"{path}[{index}]"
        item = _object(raw, item_path, keys, errors)
        if item is None:
            continue
        source_id = _text(item.get("source_id"), f"{item_path}.source_id",
                          errors, 32, pattern=_SOURCE_ID_RE)
        if source_id is not None and source_id not in source_ids:
            errors.append(f"{item_path}.source_id: fuente no declarada")
        _integer(item.get("page"), f"{item_path}.page", errors, 1, 100000,
                 nullable=True)
        _text(item.get("section"), f"{item_path}.section", errors, 160,
              nullable=True)
        _text(item.get("excerpt"), f"{item_path}.excerpt", errors, 800)


def _sources(value: Any, errors: List[str]) -> Set[str]:
    items = _array(value, "$.sources", errors, 16)
    source_ids: Set[str] = set()
    if items is None:
        return source_ids
    keys = {"id", "kind", "title", "url"}
    for index, raw in enumerate(items):
        path = f"$.sources[{index}]"
        item = _object(raw, path, keys, errors)
        if item is None:
            continue
        source_id = _text(item.get("id"), f"{path}.id", errors, 32,
                          pattern=_SOURCE_ID_RE)
        if source_id is not None:
            if source_id in source_ids:
                errors.append(f"{path}.id: fuente duplicada")
            source_ids.add(source_id)
        kind = _choice(item.get("kind"), f"{path}.kind", errors, SOURCE_KINDS)
        _text(item.get("title"), f"{path}.title", errors, 160)
        url = _text(item.get("url"), f"{path}.url", errors, 2048,
                    nullable=True)
        if kind == "web" and (url is None or not _HTTP_URL_RE.match(url)):
            errors.append(f"{path}.url: una fuente web requiere URL http o https")
    return source_ids


def _bus(value: Any, errors: List[str], source_ids: Set[str]) -> None:
    path = "$.bus"
    keys = {"baudrate", "parity", "stopbits", "evidence"}
    item = _object(value, path, keys, errors)
    if item is None:
        return
    _choice(item.get("baudrate"), f"{path}.baudrate", errors, BAUDRATES,
            nullable=True)
    _choice(item.get("parity"), f"{path}.parity", errors, PARITIES,
            nullable=True)
    _choice(item.get("stopbits"), f"{path}.stopbits", errors, {1, 2},
            nullable=True)
    _evidence(item.get("evidence"), f"{path}.evidence", errors, source_ids)


def _identity(value: Any, errors: List[str], source_ids: Set[str]) -> None:
    path = "$.identity"
    keys = {"manufacturer", "model", "revision", "evidence"}
    item = _object(value, path, keys, errors)
    if item is None:
        return
    _text(item.get("manufacturer"), f"{path}.manufacturer", errors, 80,
          nullable=True)
    _text(item.get("model"), f"{path}.model", errors, 80, nullable=True)
    _text(item.get("revision"), f"{path}.revision", errors, 80,
          nullable=True)
    _evidence(item.get("evidence"), f"{path}.evidence", errors, source_ids)


def _device(value: Any, errors: List[str], source_ids: Set[str]) -> None:
    path = "$.device"
    keys = {
        "name", "description", "default_slave_id", "desired_slave_id",
        "change_function", "change_address", "read_mode", "inter_read_ms",
        "evidence",
    }
    item = _object(value, path, keys, errors)
    if item is None:
        return
    _text(item.get("name"), f"{path}.name", errors, 16, nullable=True)
    _text(item.get("description"), f"{path}.description", errors, 256,
          nullable=True)
    default_id = _integer(item.get("default_slave_id"),
                          f"{path}.default_slave_id", errors, 1, 247,
                          nullable=True)
    desired_id = _integer(item.get("desired_slave_id"),
                          f"{path}.desired_slave_id", errors, 1, 247,
                          nullable=True)
    change_function = _choice(item.get("change_function"),
                              f"{path}.change_function", errors,
                              CHANGE_FUNCTIONS, nullable=True)
    change_address = _integer(item.get("change_address"),
                              f"{path}.change_address", errors, 0, 65535,
                              nullable=True)
    _choice(item.get("read_mode"), f"{path}.read_mode", errors, READ_MODES,
            nullable=True)
    _integer(item.get("inter_read_ms"), f"{path}.inter_read_ms", errors,
             0, 5000, nullable=True)
    _evidence(item.get("evidence"), f"{path}.evidence", errors, source_ids)
    if default_id is not None and desired_id is not None:
        changed = default_id != desired_id
        if not changed and (change_function is not None or change_address is not None):
            errors.append(f"{path}: no se admite cambio de dirección si los slave_id coinciden")


def _entries(value: Any, path: str, errors: List[str], source_ids: Set[str],
             functions: Set[str], maximum: int) -> Set[str]:
    items = _array(value, path, errors, maximum)
    ids: Set[str] = set()
    if items is None:
        return ids
    is_write = path == "$.writes"
    keys = set(_WRITE_ENTRY_PROPERTIES if is_write else _ENTRY_PROPERTIES)
    for index, raw in enumerate(items):
        item_path = f"{path}[{index}]"
        item = _object(raw, item_path, keys, errors)
        if item is None:
            continue
        entry_id = _text(item.get("id"), f"{item_path}.id", errors, 8,
                         nullable=True, minimum=2, pattern=_ID_RE)
        if entry_id is not None:
            if entry_id in ids:
                errors.append(f"{item_path}.id: identificador duplicado")
            ids.add(entry_id)
        _text(item.get("name"), f"{item_path}.name", errors, 32,
              nullable=True)
        function = _choice(item.get("function"), f"{item_path}.function",
                           errors, functions, nullable=True)
        _integer(item.get("address"), f"{item_path}.address", errors, 0,
                 65535, nullable=True)
        count = _integer(item.get("count"), f"{item_path}.count", errors, 1,
                         125, nullable=True)
        value_type = _choice(item.get("type"), f"{item_path}.type", errors,
                             VALUE_TYPES, nullable=True)
        byte_order = _choice(item.get("byte_order"),
                             f"{item_path}.byte_order", errors, BYTE_ORDERS,
                             nullable=True)
        scale = _number(item.get("scale"), f"{item_path}.scale", errors,
                        nullable=True)
        _number(item.get("offset"), f"{item_path}.offset", errors,
                nullable=True)
        unit = _text(item.get("unit"), f"{item_path}.unit", errors, 8,
                     nullable=True, minimum=0)
        if (isinstance(unit, str)
                and re.search(r"\s+(?:o|or)\s+", unit, re.IGNORECASE)):
            errors.append(
                f"{item_path}.unit: debe indicar una sola unidad efectiva")
        _evidence(item.get("evidence"), f"{item_path}.evidence", errors,
                  source_ids)
        if is_write:
            purpose = _choice(item.get("purpose"), f"{item_path}.purpose",
                              errors, {"commissioning", "operational"})
            if purpose == "commissioning":
                errors.append(
                    f"{item_path}.purpose: un ajuste de puesta en marcha debe ir en unsupported")

        if function in BIT_FUNCTIONS:
            if value_type is not None or byte_order is not None:
                errors.append(f"{item_path}: coils y entradas discretas no usan tipo ni byte_order")
            if item.get("scale") is not None or item.get("offset") is not None:
                errors.append(f"{item_path}: coils y entradas discretas no usan escala ni offset")
        elif function is not None and value_type is not None:
            expected_count = REGISTER_TYPE_COUNTS[value_type]
            if count is not None and count != expected_count:
                errors.append(f"{item_path}.count: no coincide con el tamaño del tipo")
            if value_type not in MULTI_REGISTER_TYPES and byte_order is not None:
                errors.append(f"{item_path}.byte_order: no se admite para tipos de 16 bits")

        if function in SINGLE_WRITE_FUNCTIONS and count not in (None, 1):
            errors.append(f"{item_path}.count: una escritura simple usa count 1")
        address = item.get("address")
        if (type(address) is int and type(count) is int
                and address + count > 65536):
            errors.append(f"{item_path}: el rango supera la dirección 65535")
        if path == "$.writes" and scale == 0:
            errors.append(f"{item_path}.scale: no puede ser 0 en una escritura")
    return ids


def _pending(value: Any, errors: List[str], source_ids: Set[str]) -> None:
    items = _array(value, "$.pending", errors, MAX_PROPOSAL_PENDING)
    if items is None:
        return
    keys = {
        "scope", "field", "question", "reason", "can_research_web",
        "web_query", "evidence",
    }
    for index, raw in enumerate(items):
        path = f"$.pending[{index}]"
        item = _object(raw, path, keys, errors)
        if item is None:
            continue
        _choice(item.get("scope"), f"{path}.scope", errors,
                {"bus", "device", "read", "write"})
        _text(item.get("field"), f"{path}.field", errors, 80,
              pattern=_PENDING_FIELD_RE)
        _text(item.get("question"), f"{path}.question", errors, 500)
        _text(item.get("reason"), f"{path}.reason", errors, 500)
        can_research = item.get("can_research_web")
        if not isinstance(can_research, bool):
            errors.append(f"{path}.can_research_web: debe ser booleano")
        web_query = _text(item.get("web_query"), f"{path}.web_query", errors,
                          300, nullable=True)
        if can_research is True and web_query is None:
            errors.append(f"{path}.web_query: obligatorio si se permite investigar en la web")
        if can_research is False and web_query is not None:
            errors.append(f"{path}.web_query: debe ser null si no se permite investigar en la web")
        _evidence(item.get("evidence"), f"{path}.evidence", errors, source_ids)


def _unsupported(value: Any, errors: List[str], source_ids: Set[str]) -> None:
    items = _array(value, "$.unsupported", errors, MAX_PROPOSAL_UNSUPPORTED)
    if items is None:
        return
    keys = {"category", "summary", "reason", "evidence"}
    for index, raw in enumerate(items):
        path = f"$.unsupported[{index}]"
        item = _object(raw, path, keys, errors)
        if item is None:
            continue
        _choice(item.get("category"), f"{path}.category", errors,
                UNSUPPORTED_CATEGORIES)
        _text(item.get("summary"), f"{path}.summary", errors, 500)
        _text(item.get("reason"), f"{path}.reason", errors, 500)
        _evidence(item.get("evidence"), f"{path}.evidence", errors, source_ids)


def validate_proposal(value: Any) -> Dict[str, Any]:
    """Valida estructura, límites, referencias y coherencia entre campos.

    Devuelve una copia independiente. La entrada original nunca se entrega a
    la capa que posteriormente pueda preparar cambios del formulario.
    """
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProposalValidationError([f"$: no es JSON válido ({exc})"])
    if len(encoded) > MAX_PROPOSAL_BYTES:
        raise ProposalValidationError([
            f"$: supera el límite de {MAX_PROPOSAL_BYTES} bytes",
        ])

    errors: List[str] = []
    root_keys = {
        "contract_version", "sources", "identity", "bus", "device",
        "reads", "writes", "pending", "unsupported",
    }
    root = _object(value, "$", root_keys, errors)
    if root is None:
        raise ProposalValidationError(errors)

    if root.get("contract_version") != CONTRACT_VERSION:
        errors.append("$.contract_version: versión no admitida")
    source_ids = _sources(root.get("sources"), errors)
    _identity(root.get("identity"), errors, source_ids)
    _bus(root.get("bus"), errors, source_ids)
    _device(root.get("device"), errors, source_ids)
    read_ids = _entries(root.get("reads"), "$.reads", errors, source_ids,
                        READ_FUNCTIONS, MAX_PROPOSAL_READS)
    write_ids = _entries(root.get("writes"), "$.writes", errors, source_ids,
                         WRITE_FUNCTIONS, MAX_PROPOSAL_WRITES)
    for duplicate in sorted(read_ids & write_ids):
        errors.append(f"$.writes: id {duplicate!r} ya utilizado en reads")
    _pending(root.get("pending"), errors, source_ids)
    _unsupported(root.get("unsupported"), errors, source_ids)

    if errors:
        raise ProposalValidationError(errors)
    return copy.deepcopy(root)


def application_errors(value: Any) -> List[str]:
    """Explica por qué una propuesta válida aún no se puede copiar.

    Se admite una propuesta parcial porque el asistente se abre sobre una
    tarjeta existente. Cada lectura o escritura incluida sí debe estar
    completa. El adaptador de la GUI volverá a validar el formulario combinado.
    """
    proposal = validate_proposal(value)
    errors: List[str] = []
    if proposal["pending"]:
        errors.append("quedan preguntas pendientes")

    changed = any(proposal["bus"][key] is not None
                  for key in ("baudrate", "parity", "stopbits"))
    changed = changed or any(proposal["device"][key] is not None for key in (
        "name", "description", "default_slave_id", "desired_slave_id",
        "change_function", "change_address", "read_mode", "inter_read_ms",
    ))
    changed = changed or bool(proposal["reads"] or proposal["writes"])
    if not changed:
        errors.append("la propuesta no contiene cambios aplicables")

    if any(proposal["bus"][key] is not None
           for key in ("baudrate", "parity", "stopbits")):
        if not proposal["bus"]["evidence"]:
            errors.append("los parámetros del bus no tienen evidencia")
    if any(proposal["device"][key] is not None for key in (
            "change_function", "change_address", "read_mode", "inter_read_ms")):
        if not proposal["device"]["evidence"]:
            errors.append("los parámetros del dispositivo no tienen evidencia")
    default_id = proposal["device"]["default_slave_id"]
    desired_id = proposal["device"]["desired_slave_id"]
    if (default_id is not None and desired_id is not None
            and default_id != desired_id):
        if proposal["device"]["change_function"] is None:
            errors.append("$.device.change_function: valor pendiente")
        if proposal["device"]["change_address"] is None:
            errors.append("$.device.change_address: valor pendiente")

    required = ("id", "name", "function", "address", "count")
    for collection in ("reads", "writes"):
        for index, entry in enumerate(proposal[collection]):
            path = f"$.{collection}[{index}]"
            for key in required:
                if entry[key] is None:
                    errors.append(f"{path}.{key}: valor pendiente")
            if entry["function"] not in BIT_FUNCTIONS and entry["type"] is None:
                errors.append(f"{path}.type: valor pendiente")
            if (entry["type"] in MULTI_REGISTER_TYPES
                    and entry["byte_order"] is None):
                errors.append(f"{path}.byte_order: valor pendiente")
            if not entry["evidence"]:
                errors.append(f"{path}.evidence: falta evidencia")
    return errors


def is_application_ready(value: Any) -> bool:
    """Indica si la propuesta puede pasar a la confirmación humana."""
    try:
        return not application_errors(value)
    except ProposalValidationError:
        return False
