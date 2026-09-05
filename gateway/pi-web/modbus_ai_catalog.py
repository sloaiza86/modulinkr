"""Contratos internos para descubrir y comprobar catálogos Modbus."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional, Set

from modbus_ai_contract import (
    CONTRACT_VERSION,
    MAX_PROPOSAL_PENDING,
    MAX_PROPOSAL_READS,
    MAX_PROPOSAL_UNSUPPORTED,
    MAX_PROPOSAL_WRITES,
    PROPOSAL_JSON_SCHEMA,
    ProposalValidationError,
    validate_proposal,
)


DISCOVERY_VERSION = "1.1"
MAX_DISCOVERY_SECTIONS = 32
MAX_DISCOVERY_TARGETS = 16
DOCUMENT_SCOPES = {
    "single_model",
    "product_family",
    "multi_device_system",
    "ambiguous",
}
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
_TARGET_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
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
        "document_scope",
        "targets",
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
        "document_scope": {"type": "string", "enum": sorted(DOCUMENT_SCOPES)},
        "targets": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_DISCOVERY_TARGETS,
            "items": {"$ref": "#/$defs/target"},
        },
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
        "target": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id", "label", "manufacturer", "model", "revision",
                "description", "evidence",
            ],
            "properties": {
                "id": {"type": "string", "pattern": _TARGET_ID_RE.pattern},
                "label": {"type": "string", "minLength": 1, "maxLength": 160},
                "manufacturer": {"type": "string", "minLength": 1, "maxLength": 80},
                "model": {"type": "string", "minLength": 1, "maxLength": 80},
                "revision": _nullable({"type": "string", "minLength": 1, "maxLength": 80}),
                "description": _nullable({"type": "string", "minLength": 1, "maxLength": 300}),
                "evidence": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {"$ref": "#/$defs/evidence"},
                },
            },
        },
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
                "target_ids",
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
                "target_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_DISCOVERY_TARGETS,
                    "items": {"type": "string", "pattern": _TARGET_ID_RE.pattern},
                },
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
    targets = discovery.get("targets")
    for target in targets if isinstance(targets, list) else []:
        if not isinstance(target, Mapping):
            continue
        section_evidence.append({
            "category": "other",
            "summary": str(target.get("label") or "Dispositivo detectado")[:500],
            "reason": "Identidad detectada durante el descubrimiento documental.",
            "evidence": target.get("evidence", []),
        })
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


def canonicalize_discovery_section_ids(value: Any) -> Any:
    """Asigna identificadores internos estables sin confiar en los del modelo."""
    normalized = copy.deepcopy(value)
    if not isinstance(normalized, dict):
        return normalized
    sources = normalized.get("sources")
    source_mapping: Dict[str, str] = {}
    if isinstance(sources, list):
        old_ids = [
            item.get("id") for item in sources if isinstance(item, dict)
        ]
        unique_old_ids = {
            item for item in old_ids
            if isinstance(item, str) and old_ids.count(item) == 1
        }
        for index, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                continue
            old_id = source.get("id")
            new_id = f"src{index:02d}"
            source["id"] = new_id
            if old_id in unique_old_ids:
                source_mapping[old_id] = new_id

    def remap_evidence(container: Any) -> None:
        if not isinstance(container, dict):
            return
        evidence = container.get("evidence")
        if not isinstance(evidence, list):
            return
        for item in evidence:
            if isinstance(item, dict) and item.get("source_id") in source_mapping:
                item["source_id"] = source_mapping[item["source_id"]]

    remap_evidence(normalized.get("identity"))
    targets = normalized.get("targets")
    for target in targets if isinstance(targets, list) else []:
        remap_evidence(target)
    sections = normalized.get("sections")
    if not isinstance(sections, list):
        return normalized
    for index, section in enumerate(sections, start=1):
        if isinstance(section, dict):
            if section.get("source_id") in source_mapping:
                section["source_id"] = source_mapping[section["source_id"]]
            remap_evidence(section)
            section["id"] = f"sec{index:02d}"
    return normalized


def normalize_extraction_coverage(value: Any,
                                  discovery: Mapping[str, Any]) -> Any:
    """Asigna IDs internos y reconstruye la cobertura desde la evidencia.

    Los identificadores del modelo solo sirven para correlacionar preguntas
    dentro de su respuesta. El catálogo utiliza identificadores secuenciales
    propios y asocia cada parámetro a una sección únicamente cuando fuente y
    página determinan una sola sección seleccionada. No se crean direcciones,
    funciones ni otros datos técnicos.
    """
    normalized = copy.deepcopy(value)
    if not isinstance(normalized, dict):
        return normalized
    proposal = normalized.get("proposal")
    coverage = normalized.get("coverage")
    sections = discovery.get("sections")
    if (not isinstance(proposal, dict) or not isinstance(coverage, list)
            or not isinstance(sections, list)):
        return normalized

    section_by_id = {
        item.get("id"): item for item in sections
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    coverage_by_section = {
        item.get("section_id"): item for item in coverage
        if (isinstance(item, dict)
            and item.get("section_id") in section_by_id)
    }

    def section_evidence() -> List[Dict[str, Any]]:
        for section in section_by_id.values():
            evidence = section.get("evidence")
            if isinstance(evidence, list):
                normalized_evidence = [
                    dict(item) for item in evidence
                    if isinstance(item, Mapping)
                ]
                if normalized_evidence:
                    return normalized_evidence[:8]
        return []

    unsupported = proposal.get("unsupported")
    if isinstance(unsupported, list):
        reads = proposal.get("reads")
        writes = proposal.get("writes")
        reached_limits = []
        if isinstance(reads, list) and len(reads) == MAX_PROPOSAL_READS:
            reached_limits.append(f"{MAX_PROPOSAL_READS} lecturas")
        if isinstance(writes, list) and len(writes) == MAX_PROPOSAL_WRITES:
            reached_limits.append(f"{MAX_PROPOSAL_WRITES} escrituras")
        if (reached_limits
                and len(unsupported) < MAX_PROPOSAL_UNSUPPORTED
                and not any(
                    isinstance(item, Mapping)
                    and item.get("category") == "catalog_limit"
                    for item in unsupported
                )):
            unsupported.append({
                "category": "catalog_limit",
                "summary": "La propuesta alcanzó el límite del catálogo",
                "reason": (
                    "La propuesta contiene el máximo admitido de "
                    + " y ".join(reached_limits)
                    + ". Pueden existir otros parámetros documentados que "
                    "no se incorporaron en esta propuesta."
                ),
                "evidence": section_evidence(),
            })

        if (len(unsupported) < MAX_PROPOSAL_UNSUPPORTED
                and not any(
                    isinstance(item, Mapping)
                    and item.get("category") == "data_shape"
                    for item in unsupported
                )):
            oversized_evidence = None
            oversized_section = None
            for section in section_by_id.values():
                if section.get("applicability") != "catalog":
                    continue
                for proof in section.get("evidence", []):
                    if not isinstance(proof, Mapping):
                        continue
                    excerpt = proof.get("excerpt")
                    if (isinstance(excerpt, str)
                            and re.search(
                                r"(?:^|\s)\d{1,5}\s+(?:[3-9]|\d{2,})\s+[^\d\s]",
                                excerpt,
                            )):
                        oversized_evidence = dict(proof)
                        oversized_section = section
                        break
                if oversized_evidence is not None:
                    break
            if oversized_evidence is not None:
                unsupported.append({
                    "category": "data_shape",
                    "summary": (
                        "Valor de varios registros no representable en "
                        f"{oversized_section.get('title') or 'la sección revisada'}"
                    ),
                    "reason": (
                        "La evidencia documenta una fila de más de dos registros. "
                        "El contrato actual solo representa escalares de hasta 32 bits."
                    ),
                    "evidence": [oversized_evidence],
                })
    for section_id, section in section_by_id.items():
        if (section_id in coverage_by_section
                or section.get("applicability") != "information"):
            continue
        item = {
            "section_id": section_id,
            "status": "no_applicable",
            "read_ids": [],
            "write_ids": [],
            "reason": (
                "La sección se utilizó solo como contexto técnico y no produce "
                "parámetros de catálogo."
            ),
        }
        coverage.append(item)
        coverage_by_section[section_id] = item
    declared_ids_by_section = {
        section_id: {
            "reads": set(item.get("read_ids", [])),
            "writes": set(item.get("write_ids", [])),
        }
        for section_id, item in coverage_by_section.items()
    }
    declared_sections_by_id: Dict[str, Dict[str, Set[str]]] = {
        "reads": {}, "writes": {},
    }
    for section_id, declared in declared_ids_by_section.items():
        for collection in ("reads", "writes"):
            for entry_id in declared[collection]:
                if isinstance(entry_id, str):
                    declared_sections_by_id[collection].setdefault(
                        entry_id, set()).add(section_id)

    unsupported = proposal.get("unsupported")
    if isinstance(unsupported, list):
        for section_id, item in coverage_by_section.items():
            if (item.get("status") != "incomplete"
                    or len(unsupported) >= MAX_PROPOSAL_UNSUPPORTED):
                continue
            section = section_by_id[section_id]
            declared = declared_ids_by_section.get(section_id, {})
            has_declared_entries = bool(
                declared.get("reads") or declared.get("writes"))
            unsupported.append({
                "category": "other",
                "summary": (
                    f"Revisión parcial de {section.get('title') or section_id}"
                    if has_declared_entries else
                    f"{section.get('title') or section_id} no se incluyó"
                ),
                "reason": item.get("reason") or (
                    "La fuente no pudo revisarse completamente para este grupo."),
                "evidence": section.get("evidence", []),
            })

    def evidence_sections(entry: Mapping[str, Any]) -> Set[str]:
        matches: Set[str] = set()
        evidence_items = entry.get("evidence")
        if not isinstance(evidence_items, list):
            return matches
        for proof in evidence_items:
            if not isinstance(proof, Mapping):
                continue
            source_id = proof.get("source_id")
            page = proof.get("page")
            for section_id, section in section_by_id.items():
                if section.get("source_id") != source_id:
                    continue
                page_start = section.get("page_start")
                page_end = section.get("page_end")
                if (type(page) is int and type(page_start) is int
                        and type(page_end) is int
                        and page_start <= page <= page_end):
                    matches.add(section_id)
                elif (page is None and page_start is None and page_end is None):
                    matches.add(section_id)
        return matches

    records: Dict[str, List[Dict[str, Any]]] = {"reads": [], "writes": []}
    old_to_records: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        "reads": {}, "writes": {},
    }
    for collection, prefix in (("reads", "r"), ("writes", "w")):
        entries = proposal.get(collection)
        if not isinstance(entries, list):
            continue
        unique: List[Any] = []
        fingerprints: List[Mapping[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                unique.append(entry)
                continue
            fingerprint = {
                key: item for key, item in entry.items() if key != "id"
            }
            if any(fingerprint == existing for existing in fingerprints):
                continue
            fingerprints.append(fingerprint)
            unique.append(entry)

        canonical: List[Any] = []
        for entry in unique:
            if not isinstance(entry, Mapping):
                canonical.append(entry)
                continue
            old_id = entry.get("id")
            new_id = f"{prefix}{len(records[collection]) + 1:06d}"
            item = dict(entry)
            item["id"] = new_id
            record = {
                "entry": item,
                "old_id": old_id,
                "new_id": new_id,
                "sections": evidence_sections(item),
            }
            records[collection].append(record)
            if isinstance(old_id, str):
                old_to_records[collection].setdefault(old_id, []).append(record)
            canonical.append(item)
        proposal[collection] = canonical

    def evidence_keys(value: Any) -> Set[tuple[Any, Any]]:
        return {
            (item.get("source_id"), item.get("page"))
            for item in (value if isinstance(value, list) else [])
            if isinstance(item, Mapping)
        }

    def evidence_signatures(value: Any) -> Set[tuple[Any, Any, Any, Any]]:
        return {
            (
                item.get("source_id"),
                item.get("page"),
                item.get("section"),
                " ".join(item.get("excerpt", "").split()),
            )
            for item in (value if isinstance(value, list) else [])
            if (isinstance(item, Mapping)
                and isinstance(item.get("excerpt"), str))
        }

    pending = proposal.get("pending")
    if isinstance(pending, list):
        rewritten: List[Any] = []
        for item in pending:
            if not isinstance(item, Mapping):
                rewritten.append(item)
                continue
            field = item.get("field")
            match = re.match(
                r"^(reads|writes)\.([a-z][a-z0-9_]{1,7})\.(.+)$",
                field if isinstance(field, str) else "",
            )
            if match is None:
                rewritten.append(item)
                continue
            collection, old_id, suffix = match.groups()
            candidates = old_to_records[collection].get(old_id, [])
            if len(candidates) > 1:
                pending_keys = evidence_keys(item.get("evidence"))
                evidenced = [
                    record for record in candidates
                    if pending_keys & evidence_keys(record["entry"].get("evidence"))
                ]
                if evidenced:
                    candidates = evidenced
            for record in candidates:
                if len(rewritten) >= MAX_PROPOSAL_PENDING:
                    break
                updated = dict(item)
                updated["field"] = (
                    f"{collection}.{record['new_id']}.{suffix}")
                rewritten.append(updated)
        proposal["pending"] = rewritten

    for item in coverage_by_section.values():
        item["read_ids"] = []
        item["write_ids"] = []

    for collection, coverage_key in (
            ("reads", "read_ids"), ("writes", "write_ids")):
        kept: List[Any] = [
            item for item in proposal.get(collection, [])
            if not isinstance(item, Mapping)
        ]
        for record in records[collection]:
            candidates = record["sections"]
            old_id = record["old_id"]
            declared_candidates: Set[str] = set()
            if isinstance(old_id, str):
                declared_candidates = declared_sections_by_id[
                    collection].get(old_id, set())
            if len(candidates) > 1 and isinstance(old_id, str):
                narrowed = candidates & declared_candidates
                if len(narrowed) == 1:
                    candidates = narrowed
                elif len(declared_candidates) == 1:
                    declared_id = next(iter(declared_candidates))
                    declared_section = section_by_id.get(declared_id)
                    evidence_sources = {
                        item.get("source_id")
                        for item in record["entry"].get("evidence", [])
                        if isinstance(item, Mapping)
                    }
                    if (isinstance(declared_section, Mapping)
                            and declared_section.get("source_id") in evidence_sources):
                        candidates = declared_candidates
            if len(candidates) > 1:
                entry_signatures = evidence_signatures(
                    record["entry"].get("evidence"))
                exact_sections = {
                    section_id for section_id in candidates
                    if entry_signatures & evidence_signatures(
                        section_by_id[section_id].get("evidence"))
                }
                if len(exact_sections) == 1:
                    candidates = exact_sections
            evidence_sources = {
                item.get("source_id")
                for item in record["entry"].get("evidence", [])
                if isinstance(item, Mapping)
            }
            known_section_sources = {
                item.get("source_id")
                for item in section_by_id.values()
                if isinstance(item, Mapping)
            }
            has_new_extraction_source = bool(
                evidence_sources - known_section_sources)
            if (not candidates and len(declared_candidates) == 1
                    and has_new_extraction_source):
                # La extracción web puede incorporar una fuente técnica nueva
                # que no existía durante el inventario inicial. La declaración
                # única de cobertura mantiene el parámetro dentro de una sola
                # sección seleccionada sin ampliar el alcance.
                candidates = declared_candidates
            if len(candidates) == 1:
                section_id = next(iter(candidates))
                coverage_item = coverage_by_section.get(section_id)
                if coverage_item is not None:
                    coverage_item[coverage_key].append(record["new_id"])
                    kept.append(record["entry"])
            elif candidates:
                kept.append(record["entry"])
        proposal[collection] = kept

    for coverage_item in coverage_by_section.values():
        if (coverage_item.get("status") == "no_applicable"
                and (coverage_item.get("read_ids")
                     or coverage_item.get("write_ids"))):
            coverage_item["status"] = "complete"
            coverage_item["reason"] = None

    unsupported = proposal.get("unsupported")
    for section_id, coverage_item in coverage_by_section.items():
        section = section_by_id[section_id]
        reason = coverage_item.get("reason")
        declared_ids = declared_ids_by_section.get(section_id, {})
        if (not reason
                and (declared_ids.get("reads")
                     or declared_ids.get("writes"))):
            reason = (
                "Los parámetros declarados para este grupo tenían evidencia "
                "en otras secciones y no se conservaron aquí."
            )
            coverage_item["reason"] = reason
        if (section.get("category") != "operational_control"
                or section.get("applicability") != "catalog"
                or coverage_item.get("status") != "complete"
                or coverage_item.get("read_ids")
                or coverage_item.get("write_ids")
                or not isinstance(reason, str)
                or not reason.strip()):
            continue
        coverage_item["status"] = "no_applicable"
        if (not isinstance(unsupported, list)
                or len(unsupported) >= MAX_PROPOSAL_UNSUPPORTED
                or any(
                    isinstance(item, Mapping)
                    and item.get("reason") == reason
                    for item in unsupported
                )):
            continue
        unsupported.append({
            "category": "other",
            "summary": f"{section.get('title') or section_id} no se incluyó",
            "reason": reason,
            "evidence": section.get("evidence", []),
        })

    for coverage_item in coverage_by_section.values():
        if (coverage_item.get("status") == "complete"
                and not coverage_item.get("read_ids")
                and not coverage_item.get("write_ids")
                and isinstance(coverage_item.get("reason"), str)
                and coverage_item["reason"].strip()):
            coverage_item["status"] = "no_applicable"

    return normalized


def validate_discovery(value: Any) -> Dict[str, Any]:
    """Valida identidad, fuentes, secciones y cobertura del descubrimiento."""
    errors: List[str] = []
    keys = {
        "discovery_version", "sources", "identity", "document_scope",
        "targets", "coverage_complete", "unreviewed", "sections", "summary",
    }
    root = _object(value, "$", keys, errors)
    if root is None:
        raise CatalogValidationError(errors)

    if root.get("discovery_version") != DISCOVERY_VERSION:
        errors.append("$.discovery_version: versión no admitida")
    if root.get("document_scope") not in DOCUMENT_SCOPES:
        errors.append("$.document_scope: valor no admitido")
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
    targets = root.get("targets")
    if not isinstance(targets, list):
        errors.append("$.targets: debe ser un array")
        targets = []
    elif not 1 <= len(targets) <= MAX_DISCOVERY_TARGETS:
        errors.append(
            f"$.targets: debe contener entre 1 y {MAX_DISCOVERY_TARGETS} elementos")
    target_ids: Set[str] = set()
    target_keys = {
        "id", "label", "manufacturer", "model", "revision", "description",
        "evidence",
    }
    for index, raw in enumerate(targets):
        path = f"$.targets[{index}]"
        item = _object(raw, path, target_keys, errors)
        if item is None:
            continue
        target_id = _text(item.get("id"), f"{path}.id", errors, 16,
                          pattern=_TARGET_ID_RE)
        if target_id in target_ids:
            errors.append(f"{path}.id: identificador duplicado")
        if target_id is not None:
            target_ids.add(target_id)
        _text(item.get("label"), f"{path}.label", errors, 160)
        _text(item.get("manufacturer"), f"{path}.manufacturer", errors, 80)
        _text(item.get("model"), f"{path}.model", errors, 80)
        _text(item.get("revision"), f"{path}.revision", errors, 80,
              nullable=True)
        _text(item.get("description"), f"{path}.description", errors, 300,
              nullable=True)
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{path}.evidence: debe contener evidencia")

    seen: Set[str] = set()
    section_keys = {
        "id", "title", "source_id", "page_start", "page_end", "category",
        "access", "applicability", "estimated_parameters", "target_ids",
        "evidence",
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
        section_targets = _string_list(
            item.get("target_ids"), f"{path}.target_ids", errors,
            MAX_DISCOVERY_TARGETS, 16, pattern=_TARGET_ID_RE)
        unknown_targets = sorted(set(section_targets) - target_ids)
        if unknown_targets:
            errors.append(f"{path}.target_ids: contiene un dispositivo no declarado")
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


def discovery_quality_issues(
        discovery: Mapping[str, Any], *, allow_partial_web: bool = False,
        allow_single_target_family: bool = False
        ) -> List[str]:
    """Devuelve motivos que exigen repetir el descubrimiento."""
    issues: List[str] = []
    sections = discovery.get("sections", [])
    sources = discovery.get("sources", [])
    has_web_catalog = bool(
        allow_partial_web
        and any(
            isinstance(source, Mapping) and source.get("kind") == "web"
            for source in (sources if isinstance(sources, list) else [])
        )
        and any(
            isinstance(section, Mapping)
            and section.get("applicability") == "catalog"
            for section in (sections if isinstance(sections, list) else [])
        )
        and discovery.get("unreviewed")
    )
    if discovery.get("coverage_complete") is not True and not has_web_catalog:
        issues.append("el proveedor no confirmó la revisión completa de la fuente")
    if discovery.get("unreviewed") and not has_web_catalog:
        issues.append("quedaron zonas documentales sin revisar")
    if not sections:
        issues.append("no se localizaron secciones del mapa Modbus")
    targets = discovery.get("targets", [])
    scope = discovery.get("document_scope")
    if not targets:
        issues.append("no se identificó ningún dispositivo seleccionable")
    if scope == "single_model" and len(targets) != 1:
        issues.append("el alcance de un solo modelo no coincide con los dispositivos")
    if (scope in {"product_family", "multi_device_system"}
            and len(targets) < 2 and not allow_single_target_family):
        issues.append("el alcance múltiple no identifica todos los dispositivos")
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
    if (isinstance(reads, list) and len(reads) == MAX_PROPOSAL_READS
            and not any(
                item.get("category") == "catalog_limit"
                for item in unsupported
            )):
        issues.append(
            "la propuesta alcanzó el límite de lecturas sin declarar "
            "qué parámetros quedaron fuera"
        )
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
            continue
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
