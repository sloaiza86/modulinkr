"""Contratos internos para descubrir y comprobar catálogos Modbus."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional, Set

from modbus_ai_contract import (
    CONTRACT_VERSION,
    MAX_PROPOSAL_READS,
    MAX_PROPOSAL_WRITES,
    PROPOSAL_JSON_SCHEMA,
    ProposalValidationError,
    validate_proposal,
)


DISCOVERY_VERSION = "1.0"
MAX_DISCOVERY_SECTIONS = 32
SECTION_CATEGORIES = {
    "measurement",
    "status",
    "operational_control",
    "metadata",
    "communication",
    "other",
}
SECTION_ACCESS = {"read", "write", "mixed", "none"}
SECTION_APPLICABILITY = {"catalog", "information", "unsupported", "unknown"}
COVERAGE_STATUSES = {"complete", "no_applicable", "incomplete"}
OPERATIONAL_CATEGORIES = {"measurement", "status", "operational_control"}

_SECTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
_ENTRY_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,7}$")
_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


class CatalogValidationError(ValueError):
    """Indica que una respuesta interna no cumple el contrato del catálogo."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _nullable(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


_SOURCE_SCHEMA = copy.deepcopy(PROPOSAL_JSON_SCHEMA["$defs"]["source"])
_EVIDENCE_SCHEMA = copy.deepcopy(PROPOSAL_JSON_SCHEMA["$defs"]["evidence"])
_IDENTITY_SCHEMA = copy.deepcopy(PROPOSAL_JSON_SCHEMA["$defs"]["identity"])

DISCOVERY_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ModuLinkr Modbus document discovery",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "discovery_version",
        "sources",
        "identity",
        "coverage_complete",
        "unreviewed",
        "sections",
        "summary",
    ],
    "properties": {
        "discovery_version": {"type": "string", "enum": [DISCOVERY_VERSION]},
        "sources": {
            "type": "array",
            "maxItems": 16,
            "items": {"$ref": "#/$defs/source"},
        },
        "identity": {"$ref": "#/$defs/identity"},
        "coverage_complete": {"type": "boolean"},
        "unreviewed": {
            "type": "array",
            "maxItems": 16,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
        },
        "sections": {
            "type": "array",
            "maxItems": MAX_DISCOVERY_SECTIONS,
            "items": {"$ref": "#/$defs/section"},
        },
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "$defs": {
        "source": _SOURCE_SCHEMA,
        "evidence": _EVIDENCE_SCHEMA,
        "identity": _IDENTITY_SCHEMA,
        "section": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "title",
                "source_id",
                "page_start",
                "page_end",
                "category",
                "access",
                "applicability",
                "estimated_parameters",
                "evidence",
            ],
            "properties": {
                "id": {"type": "string", "pattern": _SECTION_ID_RE.pattern},
                "title": {"type": "string", "minLength": 1, "maxLength": 160},
                "source_id": {"type": "string", "pattern": _SOURCE_ID_RE.pattern},
                "page_start": _nullable({"type": "integer", "minimum": 1}),
                "page_end": _nullable({"type": "integer", "minimum": 1}),
                "category": {"type": "string", "enum": sorted(SECTION_CATEGORIES)},
                "access": {"type": "string", "enum": sorted(SECTION_ACCESS)},
                "applicability": {
                    "type": "string",
                    "enum": sorted(SECTION_APPLICABILITY),
                },
                "estimated_parameters": _nullable({
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                }),
                "evidence": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"$ref": "#/$defs/evidence"},
                },
            },
        },
    },
}


def extraction_envelope_schema(proposal_schema: Mapping[str, Any]) -> Dict[str, Any]:
    """Envuelve el contrato final con cobertura interna por sección."""
    proposal = copy.deepcopy(dict(proposal_schema))
    proposal.pop("$schema", None)
    proposal.pop("title", None)
    definitions = proposal.pop("$defs", {})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ModuLinkr Modbus catalog extraction",
        "type": "object",
        "additionalProperties": False,
        "required": ["proposal", "coverage", "summary"],
        "properties": {
            "proposal": proposal,
            "coverage": {
                "type": "array",
                "maxItems": MAX_DISCOVERY_SECTIONS,
                "items": {"$ref": "#/$defs/coverage"},
            },
            "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "$defs": {
            **definitions,
            "coverage": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "section_id", "status", "read_ids", "write_ids", "reason",
                ],
                "properties": {
                    "section_id": {
                        "type": "string",
                        "pattern": _SECTION_ID_RE.pattern,
                    },
                    "status": {
                        "type": "string",
                        "enum": sorted(COVERAGE_STATUSES),
                    },
                    "read_ids": {
                        "type": "array",
                        "maxItems": MAX_PROPOSAL_READS,
                        "items": {"type": "string", "pattern": _ENTRY_ID_RE.pattern},
                    },
                    "write_ids": {
                        "type": "array",
                        "maxItems": MAX_PROPOSAL_WRITES,
                        "items": {"type": "string", "pattern": _ENTRY_ID_RE.pattern},
                    },
                    "reason": _nullable({
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    }),
                },
            },
        },
    }


def _object(value: Any, path: str, keys: Set[str], errors: List[str]
            ) -> Optional[Dict[str, Any]]:
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


def _text(value: Any, path: str, errors: List[str], maximum: int,
          *, nullable: bool = False, pattern: Optional[re.Pattern] = None
          ) -> Optional[str]:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        errors.append(f"{path}: debe ser texto" + (" o null" if nullable else ""))
        return None
    if not value or len(value) > maximum:
        errors.append(f"{path}: longitud fuera de 1-{maximum}")
    if any(ord(char) < 32 for char in value):
        errors.append(f"{path}: contiene caracteres de control no admitidos")
    if pattern is not None and not pattern.fullmatch(value):
        errors.append(f"{path}: formato no admitido")
    return value


def _integer(value: Any, path: str, errors: List[str], minimum: int,
             maximum: int, *, nullable: bool = False) -> Optional[int]:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{path}: debe ser entero" + (" o null" if nullable else ""))
        return None
    if value < minimum or value > maximum:
        errors.append(f"{path}: debe estar entre {minimum} y {maximum}")
    return value


def _string_list(value: Any, path: str, errors: List[str], maximum: int,
                 item_maximum: int, *, pattern: Optional[re.Pattern] = None
                 ) -> List[str]:
    if not isinstance(value, list):
        errors.append(f"{path}: debe ser un array")
        return []
    if len(value) > maximum:
        errors.append(f"{path}: admite como máximo {maximum} elementos")
    result: List[str] = []
    for index, raw in enumerate(value):
        item = _text(raw, f"{path}[{index}]", errors, item_maximum,
                     pattern=pattern)
        if item is None:
            continue
        if item in result:
            errors.append(f"{path}: contiene elementos duplicados")
        result.append(item)
    return result


def _proposal_skeleton(discovery: Mapping[str, Any]) -> Dict[str, Any]:
    section_evidence = []
    sections = discovery.get("sections")
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, Mapping):
            continue
        section_evidence.append({
            "category": "other",
            "summary": str(section.get("title") or "Sección detectada")[:500],
            "reason": "Referencia de descubrimiento documental.",
            "evidence": section.get("evidence", []),
        })
    return {
        "contract_version": CONTRACT_VERSION,
        "sources": discovery.get("sources"),
        "identity": discovery.get("identity"),
        "bus": {
            "baudrate": None,
            "parity": None,
            "stopbits": None,
            "evidence": [],
        },
        "device": {
            "name": None,
            "description": None,
            "default_slave_id": None,
            "desired_slave_id": None,
            "change_function": None,
            "change_address": None,
            "read_mode": None,
            "inter_read_ms": None,
            "evidence": [],
        },
        "reads": [],
        "writes": [],
        "pending": [],
        "unsupported": section_evidence,
    }


def validate_discovery(value: Any) -> Dict[str, Any]:
    """Valida identidad, fuentes, secciones y cobertura del descubrimiento."""
    errors: List[str] = []
    keys = {
        "discovery_version", "sources", "identity", "coverage_complete",
        "unreviewed", "sections", "summary",
    }
    root = _object(value, "$", keys, errors)
    if root is None:
        raise CatalogValidationError(errors)

    if root.get("discovery_version") != DISCOVERY_VERSION:
        errors.append("$.discovery_version: versión no admitida")
    if not isinstance(root.get("coverage_complete"), bool):
        errors.append("$.coverage_complete: debe ser booleano")
    unreviewed = _string_list(root.get("unreviewed"), "$.unreviewed", errors,
                              16, 240)
    _text(root.get("summary"), "$.summary", errors, 500)

    sections = root.get("sections")
    if not isinstance(sections, list):
        errors.append("$.sections: debe ser un array")
        sections = []
    elif len(sections) > MAX_DISCOVERY_SECTIONS:
        errors.append(
            f"$.sections: admite como máximo {MAX_DISCOVERY_SECTIONS} elementos")

    sources = root.get("sources")
    source_ids = {
        item.get("id") for item in (sources if isinstance(sources, list) else [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    seen: Set[str] = set()
    section_keys = {
        "id", "title", "source_id", "page_start", "page_end", "category",
        "access", "applicability", "estimated_parameters", "evidence",
    }
    for index, raw in enumerate(sections):
        path = f"$.sections[{index}]"
        item = _object(raw, path, section_keys, errors)
        if item is None:
            continue
        section_id = _text(item.get("id"), f"{path}.id", errors, 16,
                           pattern=_SECTION_ID_RE)
        if section_id in seen:
            errors.append(f"{path}.id: identificador duplicado")
        if section_id is not None:
            seen.add(section_id)
        _text(item.get("title"), f"{path}.title", errors, 160)
        source_id = _text(item.get("source_id"), f"{path}.source_id", errors,
                          32, pattern=_SOURCE_ID_RE)
        if source_id is not None and source_id not in source_ids:
            errors.append(f"{path}.source_id: fuente no declarada")
        page_start = _integer(item.get("page_start"), f"{path}.page_start",
                              errors, 1, 100000, nullable=True)
        page_end = _integer(item.get("page_end"), f"{path}.page_end",
                            errors, 1, 100000, nullable=True)
        if page_start is not None and page_end is not None and page_end < page_start:
            errors.append(f"{path}.page_end: no puede preceder a page_start")
        if item.get("category") not in SECTION_CATEGORIES:
            errors.append(f"{path}.category: valor no admitido")
        if item.get("access") not in SECTION_ACCESS:
            errors.append(f"{path}.access: valor no admitido")
        if item.get("applicability") not in SECTION_APPLICABILITY:
            errors.append(f"{path}.applicability: valor no admitido")
        _integer(item.get("estimated_parameters"),
                 f"{path}.estimated_parameters", errors, 0, 1000,
                 nullable=True)
        if not isinstance(item.get("evidence"), list):
            errors.append(f"{path}.evidence: debe ser un array")

    if root.get("coverage_complete") is True and unreviewed:
        errors.append("$.unreviewed: debe estar vacío cuando la cobertura es completa")

    try:
        validate_proposal(_proposal_skeleton(root))
    except ProposalValidationError as exc:
        errors.extend(f"$.discovery: {error}" for error in exc.errors)

    if errors:
        raise CatalogValidationError(errors)
    return copy.deepcopy(root)


def validate_extraction_envelope(value: Any, discovery: Mapping[str, Any],
                                 proposal: Mapping[str, Any]) -> Dict[str, Any]:
    """Valida la cobertura de una propuesta ya normalizada."""
    errors: List[str] = []
    root = _object(value, "$", {"proposal", "coverage", "summary"}, errors)
    if root is None:
        raise CatalogValidationError(errors)
    _text(root.get("summary"), "$.summary", errors, 500)
    coverage = root.get("coverage")
    if not isinstance(coverage, list):
        errors.append("$.coverage: debe ser un array")
        coverage = []
    elif len(coverage) > MAX_DISCOVERY_SECTIONS:
        errors.append(
            f"$.coverage: admite como máximo {MAX_DISCOVERY_SECTIONS} elementos")

    section_ids = {
        item.get("id") for item in discovery.get("sections", [])
        if isinstance(item, Mapping)
    }
    read_ids = {
        item.get("id") for item in proposal.get("reads", [])
        if isinstance(item, Mapping)
    }
    write_ids = {
        item.get("id") for item in proposal.get("writes", [])
        if isinstance(item, Mapping)
    }
    read_reference_counts: Dict[str, int] = {}
    write_reference_counts: Dict[str, int] = {}
    seen: Set[str] = set()
    normalized_coverage: List[Dict[str, Any]] = []
    keys = {"section_id", "status", "read_ids", "write_ids", "reason"}
    for index, raw in enumerate(coverage):
        path = f"$.coverage[{index}]"
        item = _object(raw, path, keys, errors)
        if item is None:
            continue
        section_id = _text(item.get("section_id"), f"{path}.section_id",
                           errors, 16, pattern=_SECTION_ID_RE)
        if section_id not in section_ids:
            errors.append(f"{path}.section_id: sección no declarada")
        if section_id in seen:
            errors.append(f"{path}.section_id: sección duplicada")
        if section_id is not None:
            seen.add(section_id)
        status = item.get("status")
        if status not in COVERAGE_STATUSES:
            errors.append(f"{path}.status: valor no admitido")
        item_reads = _string_list(item.get("read_ids"), f"{path}.read_ids",
                                  errors, MAX_PROPOSAL_READS, 8,
                                  pattern=_ENTRY_ID_RE)
        item_writes = _string_list(item.get("write_ids"), f"{path}.write_ids",
                                   errors, MAX_PROPOSAL_WRITES, 8,
                                   pattern=_ENTRY_ID_RE)
        reason = _text(item.get("reason"), f"{path}.reason", errors, 500,
                       nullable=True)
        if status in {"no_applicable", "incomplete"} and reason is None:
            errors.append(
                f"{path}.reason: obligatorio cuando status es {status}")
        if status == "no_applicable" and (item_reads or item_writes):
            errors.append(
                f"{path}: no_applicable no puede declarar parámetros producidos")
        for entry_id in item_reads:
            read_reference_counts[entry_id] = (
                read_reference_counts.get(entry_id, 0) + 1)
        for entry_id in item_writes:
            write_reference_counts[entry_id] = (
                write_reference_counts.get(entry_id, 0) + 1)
        normalized_coverage.append({
            "section_id": section_id,
            "status": status,
            "read_ids": item_reads,
            "write_ids": item_writes,
            "reason": reason,
            "missing_read_ids": sorted(set(item_reads) - read_ids),
            "missing_write_ids": sorted(set(item_writes) - write_ids),
        })

    for entry_id in sorted(read_ids):
        count = read_reference_counts.get(entry_id, 0)
        if count == 0:
            errors.append(
                f"$.coverage: la lectura {entry_id} no tiene sección de cobertura")
        elif count > 1:
            errors.append(
                f"$.coverage: la lectura {entry_id} pertenece a varias secciones")
    for entry_id in sorted(write_ids):
        count = write_reference_counts.get(entry_id, 0)
        if count == 0:
            errors.append(
                f"$.coverage: la escritura {entry_id} no tiene sección de cobertura")
        elif count > 1:
            errors.append(
                f"$.coverage: la escritura {entry_id} pertenece a varias secciones")

    if errors:
        raise CatalogValidationError(errors)
    return {
        "coverage": normalized_coverage,
        "summary": root["summary"],
    }


def discovery_quality_issues(discovery: Mapping[str, Any]) -> List[str]:
    """Devuelve motivos que exigen repetir el descubrimiento."""
    issues: List[str] = []
    if discovery.get("coverage_complete") is not True:
        issues.append("el proveedor no confirmó la revisión completa de la fuente")
    if discovery.get("unreviewed"):
        issues.append("quedaron zonas documentales sin revisar")
    if not discovery.get("sections"):
        issues.append("no se localizaron secciones del mapa Modbus")
    return issues


def extraction_quality_issues(discovery: Mapping[str, Any],
                              proposal: Mapping[str, Any],
                              extraction: Mapping[str, Any],
                              *, discarded_entries: Any = None) -> List[str]:
    """Explica por qué una extracción no puede mostrarse como catálogo final."""
    issues: List[str] = []
    reads = proposal.get("reads", [])
    writes = proposal.get("writes", [])
    if not reads and not writes:
        issues.append("la extracción no produjo lecturas ni escrituras aplicables")

    unsupported = [
        item for item in proposal.get("unsupported", [])
        if isinstance(item, Mapping)
    ]
    surviving_ids = {
        item.get("id")
        for collection in (reads, writes)
        for item in (collection if isinstance(collection, list) else [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    explained_missing_ids = {"reads": set(), "writes": set()}
    if isinstance(discarded_entries, list):
        for discarded in discarded_entries:
            if not isinstance(discarded, Mapping):
                issues.append("el informe de parámetros descartados está incompleto")
                continue
            category = discarded.get("category")
            reason = discarded.get("reason")
            explained = any(
                item.get("category") == category and item.get("reason") == reason
                for item in unsupported
            )
            if not explained:
                issues.append(
                    "la normalización descartó parámetros sin explicar el motivo")
            collection = discarded.get("collection")
            entry_id = discarded.get("id")
            if collection not in explained_missing_ids:
                issues.append("el informe de parámetros descartados está incompleto")
                continue
            if isinstance(entry_id, str):
                if entry_id in surviving_ids:
                    issues.append(
                        "la normalización detectó identificadores duplicados o ambiguos")
                elif explained:
                    explained_missing_ids[collection].add(entry_id)
    elif (type(discarded_entries) is int and discarded_entries > 0
          and not unsupported):
        issues.append("la normalización descartó parámetros sin explicar el motivo")

    coverage_by_section = {
        item.get("section_id"): item for item in extraction.get("coverage", [])
        if isinstance(item, Mapping)
    }
    for section in discovery.get("sections", []):
        if not isinstance(section, Mapping):
            continue
        section_id = section.get("id")
        coverage = coverage_by_section.get(section_id)
        if coverage is None:
            issues.append(f"la sección {section_id} no tiene resultado de cobertura")
            continue
        if coverage.get("status") == "incomplete":
            issues.append(f"la sección {section_id} quedó incompleta")
        missing_reads = (
            set(coverage.get("missing_read_ids", []))
            - explained_missing_ids["reads"]
        )
        missing_writes = (
            set(coverage.get("missing_write_ids", []))
            - explained_missing_ids["writes"]
        )
        if missing_reads or missing_writes:
            issues.append(f"la sección {section_id} referencia parámetros descartados")
        if (section.get("applicability") == "catalog"
                and section.get("category") in OPERATIONAL_CATEGORIES
                and not coverage.get("read_ids")
                and not coverage.get("write_ids")):
            if coverage.get("status") == "no_applicable":
                if not coverage.get("reason"):
                    issues.append(
                        f"la sección operativa {section_id} no explica por qué "
                        "no tiene parámetros aplicables")
                continue
            issues.append(f"la sección operativa {section_id} no produjo parámetros")
    return list(dict.fromkeys(issues))
