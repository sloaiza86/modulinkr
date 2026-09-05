"""Pruebas offline del flujo escalonado del asistente Modbus."""

from __future__ import annotations

import base64
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PI_WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI_WEB))

from modbus_ai_catalog import (  # noqa: E402
    CatalogValidationError,
    DISCOVERY_JSON_SCHEMA,
    DISCOVERY_VERSION,
    canonicalize_discovery_section_ids,
    discovery_quality_issues,
    extraction_envelope_schema,
    extraction_quality_issues,
    normalize_extraction_coverage,
    validate_discovery,
    validate_extraction_envelope,
)
from modbus_ai_contract import CONTRACT_VERSION, validate_proposal  # noqa: E402
from modbus_ai_provider import (  # noqa: E402
    AssistantRequestError,
    DISCOVERY_SYSTEM_PROMPT,
    DISCOVERY_MAX_OUTPUT_TOKENS,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_MAX_OUTPUT_TOKENS,
    ProviderCallError,
    SYSTEM_PROMPT,
    _append_detected_non_operational_sections,
    _discover_remote_technical_sources,
    _drop_unused_unopened_web_sources,
    _expand_evidenced_read_write_operations,
    _compact_global_refinement,
    _compact_global_byte_order_refinement,
    _canonicalize_source_ids,
    _declare_current_evidence_source,
    _identity_web_search_issues,
    _identity_issues,
    _normalize_provider_proposal,
    _normalize_discovery_sections,
    _merge_refinement_with_previous,
    _prepare_pending_research,
    _propagate_global_refinement,
    _propagate_global_byte_order,
    _remote_source_files,
    _remote_upload_filename,
    _reconcile_discovery_evidence_sources,
    _retain_discovery_reference_sources,
    _response_from_sse_lines,
    _restore_discovery_source_metadata,
    _sanitize_discovery_user_texts,
    build_discovery_payload,
    build_extraction_payload,
    prepare_provider_payload,
    request_proposal,
    validate_assistant_request,
)


CONFIG = {
    "provider": "openai",
    "model": "gpt-5.6",
    "base_url": "https://api.openai.com/v1",
}


def evidence(source_id="manual-1", page=12, section="Mapa Modbus"):
    return {
        "source_id": source_id,
        "page": page,
        "section": section,
        "excerpt": "El registro documenta un valor Modbus del dispositivo.",
    }


def source(kind="manual"):
    return {
        "id": "manual-1",
        "kind": kind,
        "title": "Manual técnico del dispositivo",
        "url": ("https://example.test/device/modbus" if kind == "web" else None),
    }


def identity(source_id="manual-1"):
    return {
        "manufacturer": "Example Instruments",
        "model": "Meter 100",
        "revision": "1.0",
        "evidence": [evidence(source_id)],
    }


def target(target_id="meter100", source_id="manual-1"):
    return {
        "id": target_id,
        "label": "Example Instruments Meter 100",
        "manufacturer": "Example Instruments",
        "model": "Meter 100",
        "revision": "1.0",
        "description": "Medidor Modbus de ejemplo",
        "evidence": [evidence(source_id)],
    }


def section(section_id, category, *, access="read", applicability="catalog",
            estimated_parameters=1, source_id="manual-1", page=12,
            target_ids=None):
    return {
        "id": section_id,
        "title": f"Sección {section_id}",
        "source_id": source_id,
        "page_start": page,
        "page_end": page + 1,
        "category": category,
        "access": access,
        "applicability": applicability,
        "estimated_parameters": estimated_parameters,
        "target_ids": list(target_ids or ["meter100"]),
        "evidence": [evidence(source_id, page)],
    }


def discovery(*sections, kind="manual", coverage_complete=True,
              unreviewed=None):
    return {
        "discovery_version": DISCOVERY_VERSION,
        "sources": [source(kind)],
        "identity": identity(),
        "document_scope": "single_model",
        "targets": [target(source_id="manual-1")],
        "coverage_complete": coverage_complete,
        "unreviewed": list(unreviewed or []),
        "sections": list(sections),
        "summary": "Se revisaron las secciones Modbus de la fuente.",
    }


def read_entry(entry_id, name, address, *, value_type="float32", count=2,
               byte_order="ABCD", source_id="manual-1", page=12):
    return {
        "id": entry_id,
        "name": name,
        "function": "read_holding_registers",
        "address": address,
        "count": count,
        "type": value_type,
        "byte_order": byte_order,
        "scale": 1,
        "offset": 0,
        "unit": None,
        "evidence": [evidence(source_id, page)],
    }


def proposal(*reads, kind="manual"):
    return {
        "contract_version": CONTRACT_VERSION,
        "sources": [source(kind)],
        "identity": identity(),
        "bus": {
            "baudrate": None,
            "parity": None,
            "stopbits": None,
            "evidence": [],
        },
        "device": {
            "name": "meter_100",
            "description": "Medidor Modbus",
            "default_slave_id": 1,
            "desired_slave_id": 1,
            "change_function": None,
            "change_address": None,
            "read_mode": "grouped",
            "inter_read_ms": 250,
            "evidence": [evidence()],
        },
        "reads": list(reads),
        "writes": [],
        "pending": [],
        "unsupported": [],
    }


def coverage(section_id, *, status="complete", read_ids=None,
             write_ids=None, reason=None):
    return {
        "section_id": section_id,
        "status": status,
        "read_ids": list(read_ids or []),
        "write_ids": list(write_ids or []),
        "reason": reason,
    }


def extraction(value, *coverage_items, summary="Extracción completada."):
    return {
        "proposal": value,
        "coverage": list(coverage_items),
        "summary": summary,
    }


def provider_response(value, *, search_query=None, opened_url=None):
    output = []
    if search_query is not None:
        output.append({
            "type": "web_search_call",
            "action": {
                "type": "search",
                "query": search_query,
                "queries": [search_query],
            },
        })
    if opened_url is not None:
        output.append({
            "type": "web_search_call",
            "action": {
                "type": "open_page",
                "url": opened_url,
            },
        })
    output.append({
        "type": "message",
        "content": [{
            "type": "output_text",
            "text": json.dumps(value, ensure_ascii=False),
        }],
    })
    return {
        "status": "completed",
        "output": output,
    }


def request_body(*, kind="manual", operation="discover", previous=None,
                 selected_reads=None, found=None, selected_sections=None):
    if kind == "manual":
        request_source = {
            "kind": "manual",
            "manufacturer": None,
            "model": None,
            "filename": "manual.pdf",
            "pdf_base64": base64.b64encode(
                b"%PDF-1.7\nstrict offline fixture"
            ).decode("ascii"),
        }
    else:
        request_source = {
            "kind": "identity",
            "manufacturer": "Example Instruments",
            "model": "Meter 100",
            "filename": None,
            "pdf_base64": None,
        }
    return {
        "operation": operation,
        "source": request_source,
        "confirmed_identity": ({
            "manufacturer": "Example Instruments",
            "model": "Meter 100",
            "revision": "1.0",
        } if operation == "extract" else None),
        "current": {
            "bus": {"baudrate": 9600, "parity": "N", "stopbits": 1},
            "device": {"name": "", "default_slave_id": 1,
                       "desired_slave_id": 1},
        },
        "discovery": found if operation == "extract" else None,
        "target_id": "meter100" if operation == "extract" else None,
        "selected_sections": list(selected_sections or (
            ["measurements"] if operation == "extract" else [])),
        "previous_proposal": previous,
        "selected": {
            "reads": list(selected_reads or []),
            "writes": [],
        },
        "answers": [],
        "web_queries": [],
    }


def validated_request(**kwargs):
    return validate_assistant_request(request_body(**kwargs))


class CatalogContractTests(unittest.TestCase):
    def test_input_register_abbreviation_preserves_the_read_function(self):
        value = proposal(read_entry("temp", "Temperatura", 1))
        value["sources"][0].update({
            "kind": "web",
            "url": "https://example.test/xy-md02",
        })
        value["reads"][0]["function"] = "read_input_registers"
        value["reads"][0]["evidence"][0]["excerpt"] = (
            "Input Reg. | Temperature | 2 | 0x0001")

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(
            "read_input_registers", normalized["reads"][0]["function"])

    def test_unknown_manufacturer_is_compatible_with_an_absent_manufacturer(self):
        self.assertEqual([], _identity_issues(
            {"manufacturer": "desconocido", "model": "XY-MD02"},
            {"manufacturer": None, "model": "XY-MD02"},
            label="extracción",
        ))
        self.assertEqual([], _identity_issues(
            {"manufacturer": "null", "model": "XY-MD02"},
            {"manufacturer": None, "model": "XY-MD02"},
            label="extracción",
        ))

    def test_unknown_target_manufacturer_matches_an_absent_manufacturer(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["identity"]["manufacturer"] = None
        found["targets"][0]["manufacturer"] = "desconocido"
        body = request_body(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"])
        body["source"]["manufacturer"] = "Generico"
        body["confirmed_identity"]["manufacturer"] = "desconocido"

        request = validate_assistant_request(body)

        self.assertIsNone(request["source"]["manufacturer"])
        self.assertIsNone(request["confirmed_identity"]["manufacturer"])

    def test_discovery_removes_markdown_citations_from_visible_text(self):
        value = discovery(section("measurements", "measurement"), kind="web")
        value["targets"][0]["description"] = (
            "Sensor Modbus. ([example.com](https://example.com/x?utm_source=openai))")
        value["summary"] = (
            "Inventario técnico ([example.com](https://example.com/x)).")
        value["targets"][0]["manufacturer"] = "null"

        _sanitize_discovery_user_texts(value)

        self.assertEqual("Sensor Modbus.", value["targets"][0]["description"])
        self.assertEqual("Inventario técnico.", value["summary"])
        self.assertEqual(
            "desconocido", value["targets"][0]["manufacturer"])

    def _assert_strict_schema(self, schema):
        if not isinstance(schema, dict):
            return
        if schema.get("type") == "object":
            self.assertFalse(schema.get("additionalProperties", True))
            self.assertEqual(
                set(schema.get("properties", {})),
                set(schema.get("required", [])),
            )
        for key, value in schema.items():
            if key in {"properties", "$defs"}:
                for child in value.values():
                    self._assert_strict_schema(child)
            elif key in {"items"}:
                self._assert_strict_schema(value)
            elif key in {"anyOf", "allOf", "oneOf"}:
                for child in value:
                    self._assert_strict_schema(child)

    def test_internal_provider_schemas_are_strict_at_every_object(self):
        self._assert_strict_schema(DISCOVERY_JSON_SCHEMA)
        from modbus_ai_contract import PROPOSAL_JSON_SCHEMA
        self._assert_strict_schema(
            extraction_envelope_schema(PROPOSAL_JSON_SCHEMA))

    def test_invalid_discovery_types_are_rejected_without_crashing(self):
        for field in ("sources", "sections"):
            invalid = discovery(
                section("measurements", "measurement"),
            )
            invalid[field] = None
            with self.assertRaises(CatalogValidationError):
                validate_discovery(invalid)

    def test_extract_accepts_all_sections_allowed_by_discovery_contract(self):
        sections = [
            section(f"sec{index:02d}", "measurement")
            for index in range(1, 21)
        ]
        found = discovery(*sections)
        selected = [item["id"] for item in sections]

        validated = validate_assistant_request(request_body(
            operation="extract", found=found, selected_sections=selected,
        ))

        self.assertEqual(selected, validated["selected_sections"])

    def test_product_family_keeps_common_and_variant_specific_sections(self):
        found = discovery(
            section("common", "measurement",
                    target_ids=["meter100", "meter200"]),
            section("advanced", "status", target_ids=["meter200"]),
        )
        found["document_scope"] = "product_family"
        second = target("meter200")
        second["label"] = "Example Instruments Meter 200"
        second["model"] = "Meter 200"
        found["targets"].append(second)

        checked = validate_discovery(found)

        self.assertEqual(
            ["meter100", "meter200"], checked["sections"][0]["target_ids"])
        self.assertEqual(["meter200"], checked["sections"][1]["target_ids"])

    def test_unknown_section_target_is_rejected(self):
        found = discovery(section(
            "measurements", "measurement", target_ids=["not_declared"]))
        with self.assertRaises(CatalogValidationError) as raised:
            validate_discovery(found)
        self.assertIn(
            "$.sections[0].target_ids: contiene un dispositivo no declarado",
            raised.exception.errors,
        )

    def test_discovery_canonicalizes_url_source_ids_and_all_references(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        url = "https://example.test/device/manual.pdf"
        found["sources"][0]["id"] = url
        found["identity"]["evidence"][0]["source_id"] = url
        found["targets"][0]["evidence"][0]["source_id"] = url
        found["sections"][0]["source_id"] = url
        found["sections"][0]["evidence"][0]["source_id"] = url

        checked = validate_discovery(
            canonicalize_discovery_section_ids(found))

        self.assertEqual("src01", checked["sources"][0]["id"])
        self.assertEqual("src01", checked["identity"]["evidence"][0]["source_id"])
        self.assertEqual("src01", checked["targets"][0]["evidence"][0]["source_id"])
        self.assertEqual("src01", checked["sections"][0]["source_id"])
        self.assertEqual("src01", checked["sections"][0]["evidence"][0]["source_id"])

    def test_manual_is_attached_to_both_initial_stages(self):
        found = discovery(
            section("measurements", "measurement"),
        )
        discovery_request = validated_request()
        extraction_request = validated_request(
            operation="extract", found=found,
            selected_sections=["measurements"])
        for payload in (
                build_discovery_payload(discovery_request, "gpt-5.6"),
                build_extraction_payload(
                    extraction_request, "gpt-5.6", found)):
            content = payload["input"][1]["content"]
            self.assertEqual("input_file", content[1]["type"])
            self.assertTrue(content[1]["file_data"].startswith(
                "data:application/pdf;base64,"))
            self.assertNotIn("pdf_base64", content[0]["text"])

    def test_manual_provider_file_mode_omits_inline_pdf_in_both_stages(self):
        found = discovery(section("measurements", "measurement"))
        discovery_request = validated_request()
        extraction_request = validated_request(
            operation="extract", found=found,
            selected_sections=["measurements"],
        )
        for payload in (
                build_discovery_payload(
                    discovery_request,
                    "gpt-5.6",
                    allow_code_interpreter=True,
                    uploaded_file_ids=["file-manual123"],
                ),
                build_extraction_payload(
                    extraction_request,
                    "gpt-5.6",
                    found,
                    allow_code_interpreter=True,
                    uploaded_file_ids=["file-manual123"],
                )):
            content = payload["input"][1]["content"]
            self.assertFalse(any(
                item.get("type") == "input_file" for item in content))
            self.assertEqual(
                ["file-manual123"],
                payload["tools"][0]["container"]["file_ids"],
            )
            self.assertEqual("required", payload["tool_choice"])

    def test_extraction_reserves_output_for_the_full_contract(self):
        found = discovery(section("measurements", "measurement"))
        request = validated_request(
            operation="extract", found=found,
            selected_sections=["measurements"])

        payload = build_extraction_payload(request, "gpt-5.6", found)

        self.assertEqual(
            EXTRACTION_MAX_OUTPUT_TOKENS, payload["max_output_tokens"])
        self.assertGreater(EXTRACTION_MAX_OUTPUT_TOKENS, 18000)

    def test_evaluator_can_lower_provider_output_limit(self):
        found = discovery(section("measurements", "measurement"))
        request = validated_request()
        with patch(
                "modbus_ai_provider.post_responses",
                return_value=provider_response(found)) as mocked, patch(
                "modbus_ai_provider._upload_provider_file",
                return_value="file-manual-test"), patch(
                "modbus_ai_provider._delete_provider_file"):
            request_proposal(
                CONFIG,
                "offline-test-key",
                request,
                security_mode="production",
                max_output_tokens=1024,
            )

        self.assertEqual(1024, mocked.call_args.args[2]["max_output_tokens"])

    def test_identity_extraction_includes_concrete_required_web_queries(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"])

        payload = build_extraction_payload(request, "gpt-5.6", found)
        prompt = payload["input"][1]["content"][0]["text"]

        self.assertIn("required_web_queries", prompt)
        self.assertIn("Example Instruments", prompt)
        self.assertIn("Meter 100", prompt)
        self.assertIn("Modbus register list", prompt)

    def test_identity_extraction_omits_unknown_manufacturer_from_queries(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["identity"]["manufacturer"] = None
        found["targets"][0]["manufacturer"] = "null"
        body = request_body(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"])
        body["source"]["manufacturer"] = None
        body["confirmed_identity"]["manufacturer"] = None
        request = validate_assistant_request(body)

        payload = build_extraction_payload(request, "gpt-5.6", found)
        prompt = payload["input"][1]["content"][0]["text"]
        prompt_data = json.loads(prompt.split("\n", 1)[1])
        queries = prompt_data["required_web_queries"]

        self.assertIn('"Meter 100" Modbus register list', queries[0])
        self.assertNotIn('"" "Meter 100"', queries[0])
        self.assertNotIn('"null"', " ".join(queries))

    def test_openai_identity_extraction_attaches_remote_technical_source(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["sources"][0]["url"] = (
            "https://manufacturer.example/register-list.xlsx")
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"])

        payload = build_extraction_payload(
            request, "gpt-5.6", found, allow_code_interpreter=True)

        files = [
            item for item in payload["input"][1]["content"]
            if item["type"] == "input_file"
        ]
        self.assertEqual([{
            "type": "input_file",
            "file_url": "https://manufacturer.example/register-list.xlsx",
            "detail": "auto",
        }], files)
        self.assertEqual(
            {"code_interpreter"}, {tool["type"] for tool in payload["tools"]})
        self.assertNotIn("max_tool_calls", payload)
        self.assertNotIn("web_search", {
            tool["type"] for tool in payload["tools"]})
        self.assertIn(
            "archivo técnico adjunto como fuente principal",
            payload["input"][1]["content"][0]["text"],
        )
        self.assertIn(
            "No te detengas tras comprobar que el archivo existe",
            payload["input"][1]["content"][0]["text"],
        )
        self.assertIn(
            "engine='openpyxl'",
            payload["input"][1]["content"][0]["text"],
        )

    def test_compatible_identity_extraction_does_not_require_openai_tool(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"])

        payload = build_extraction_payload(
            request, "compatible-model", found,
            allow_code_interpreter=False)

        self.assertEqual(
            {"web_search"}, {tool["type"] for tool in payload["tools"]})
        self.assertFalse(any(
            item["type"] == "input_file"
            for item in payload["input"][1]["content"]
        ))

    def test_identity_extraction_rejects_an_irrelevant_calculator_call(self):
        request = validated_request(
            kind="identity", operation="extract",
            found=discovery(section("measurements", "measurement"), kind="web"),
        )
        response = {"output": [{
            "type": "web_search_call",
            "action": {
                "type": "search",
                "query": "calculator: 1+1",
                "queries": ["calculator: 1+1"],
            },
        }]}

        issues = _identity_web_search_issues(response, request)

        self.assertEqual(1, len(issues))

    def test_identity_extraction_accepts_a_device_specific_web_search(self):
        request = validated_request(
            kind="identity", operation="extract",
            found=discovery(section("measurements", "measurement"), kind="web"),
        )
        response = {"output": [{
            "type": "web_search_call",
            "action": {
                "type": "search",
                "query": "Example Instruments Meter 100 Modbus register list",
                "queries": [
                    "Example Instruments Meter 100 Modbus register list"],
            },
        }]}

        self.assertEqual([], _identity_web_search_issues(response, request))

    def test_identity_extraction_accepts_an_attached_technical_file(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["sources"][0]["url"] = "https://example.test/register-map.xlsx"
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"])

        self.assertEqual([], _identity_web_search_issues({"output": []}, request))

    def test_identity_extraction_accepts_opening_a_known_pdf_source(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["sources"][0]["url"] = "https://example.test/register-map.pdf"
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"])
        response = {"output": [{
            "type": "web_search_call",
            "action": {
                "type": "open_page",
                "url": "https://example.test/register-map.pdf",
            },
        }]}

        self.assertEqual([], _identity_web_search_issues(response, request))

    def test_exact_identity_allows_one_target_from_family_documentation(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["document_scope"] = "product_family"

        self.assertIn(
            "el alcance múltiple no identifica todos los dispositivos",
            discovery_quality_issues(found),
        )
        self.assertEqual([], discovery_quality_issues(
            found,
            allow_single_target_family=True,
        ))

    def test_landing_page_discovers_extensionless_technical_attachment(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["sources"][0]["url"] = "https://example.test/register-list"
        page = (
            '{"title":"PublicModbusRegisterList.xlsx",'
            '"url":"https:\\/\\/files.example.test\\/download\\/123"}'
        )

        with patch(
                "modbus_ai_provider._fetch_public_source_index",
                return_value=page):
            _discover_remote_technical_sources(found)

        self.assertEqual(2, len(found["sources"]))
        self.assertEqual(
            "https://files.example.test/download/123",
            found["sources"][1]["url"],
        )
        self.assertEqual([{
            "type": "input_file",
            "file_url": "https://files.example.test/download/123",
            "detail": "auto",
        }], _remote_source_files(found))

    def test_generic_remote_register_list_becomes_selectable_categories(self):
        generic = section("register-file", "metadata")
        generic.update({
            "title": "Archivo adjunto de lista de registros Modbus",
            "access": "none",
        })
        found = discovery(generic, kind="web")
        found["sources"][0]["url"] = "https://example.test/register-list"
        page = (
            '{"name":"PublicModbusRegisterList.xlsx",'
            '"url":"https:\\/\\/files.example.test\\/download\\/123"}'
        )

        with patch(
                "modbus_ai_provider._fetch_public_source_index",
                return_value=page):
            _discover_remote_technical_sources(found)

        catalog_sections = [
            item for item in found["sections"]
            if item["applicability"] == "catalog"
        ]
        self.assertEqual(3, len(catalog_sections))
        self.assertEqual({
            "measurement", "status", "operational_control",
        }, {item["category"] for item in catalog_sections})
        self.assertTrue(all(
            item["source_id"] == "src02" for item in catalog_sections))

    def test_unrelated_remote_file_does_not_replace_concrete_catalog(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["sources"].append({
            "id": "src02",
            "kind": "web",
            "title": "Especificación general de Modbus.pdf",
            "url": "https://example.test/modbus-protocol.pdf",
        })

        _discover_remote_technical_sources(found)

        catalog_sections = [
            item for item in found["sections"]
            if item["applicability"] == "catalog"
        ]
        self.assertEqual(["measurements"], [
            item["id"] for item in catalog_sections])
        self.assertEqual([], _remote_source_files(found, ["measurements"]))

    def test_unrelated_remote_file_is_not_attached_to_extraction(self):
        found = discovery(section("measurements", "measurement"), kind="web")
        found["sources"][0]["url"] = "https://example.test/device-registers"
        found["sources"].append({
            "id": "src02",
            "kind": "web",
            "title": "Especificación general de Modbus.pdf",
            "url": "https://example.test/modbus-protocol.pdf",
        })
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"])

        payload = build_extraction_payload(
            request, "gpt-5.6", found, allow_code_interpreter=True)

        self.assertEqual({"web_search"}, {
            tool["type"] for tool in payload["tools"]})
        self.assertFalse(any(
            item["type"] == "input_file"
            for item in payload["input"][1]["content"]
        ))

    def test_remote_register_list_recovers_discovery_without_sections(self):
        found = discovery(section("placeholder", "metadata"), kind="web")
        found["sections"] = []
        found["sources"][0]["url"] = "https://example.test/register-list"
        page = (
            '{"name":"PublicModbusRegisterList.xlsx",'
            '"url":"https:\\/\\/files.example.test\\/download\\/123"}'
        )

        with patch(
                "modbus_ai_provider._fetch_public_source_index",
                return_value=page):
            _discover_remote_technical_sources(found)

        self.assertEqual(3, len(found["sections"]))
        self.assertEqual({found["targets"][0]["id"]}, {
            target_id
            for item in found["sections"]
            for target_id in item["target_ids"]
        })
        self.assertTrue(all(
            item["source_id"] == "src02" for item in found["sections"]))

    def test_direct_pdf_becomes_relayable_generic_catalog(self):
        found = discovery(
            section("slave-address", "metadata"), kind="web")
        found["sections"][0].update({
            "title": "Registro de dirección del esclavo",
            "applicability": "catalog",
        })
        found["sources"][0].update({
            "title": "Manual técnico del equipo",
            "url": "https://manufacturer.example/device-manual.pdf",
        })

        _normalize_discovery_sections(found)
        _discover_remote_technical_sources(found)

        selectable = [
            item for item in found["sections"]
            if item["applicability"] == "catalog"
        ]
        self.assertEqual(
            {"measurement", "status", "operational_control"},
            {item["category"] for item in selectable},
        )
        self.assertEqual("communication", found["sections"][0]["category"])
        self.assertEqual("information", found["sections"][0]["applicability"])
        self.assertEqual([{
            "type": "input_file",
            "file_url": "https://manufacturer.example/device-manual.pdf",
            "detail": "auto",
        }], _remote_source_files(found))
        self.assertEqual(
            "device-manual.pdf",
            _remote_upload_filename(found["sources"][0]),
        )

    def test_discovery_estimates_do_not_limit_section_selection(self):
        found = discovery(
            section("energy", "measurement", estimated_parameters=20),
            section("realtime", "measurement", estimated_parameters=28,
                    page=15),
        )

        request = validated_request(
            operation="extract",
            found=found,
            selected_sections=["energy", "realtime"],
        )

        self.assertEqual(["energy", "realtime"], request["selected_sections"])

    def test_strict_discovery_and_extraction_fixtures_are_valid(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement", estimated_parameters=2),
        ))
        voltage = read_entry("voltage", "Tensión", 100)
        current = read_entry("current", "Corriente", 102)
        proposed = validate_proposal(proposal(voltage, current))
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage("measurements", read_ids=["voltage", "current"]),
            ),
            found,
            proposed,
        )
        self.assertEqual([], discovery_quality_issues(found))
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_bit_functions_discard_register_only_metadata_without_losing_entries(self):
        digital_input = {
            "id": "input1",
            "name": "Entrada digital 1",
            "function": "read_discrete_inputs",
            "address": 0,
            "count": 1,
            "type": "uint16",
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
        }
        digital_output = {
            "id": "output1",
            "name": "Salida digital 1",
            "function": "write_single_coil",
            "address": 0,
            "count": 1,
            "type": "uint16",
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
            "purpose": "operational",
        }
        value = proposal(digital_input)
        value["writes"] = [digital_output]

        normalized = _normalize_provider_proposal(value)
        validated = validate_proposal(normalized)

        self.assertEqual(1, len(validated["reads"]))
        self.assertEqual(1, len(validated["writes"]))
        for entry in validated["reads"] + validated["writes"]:
            for field in ("type", "byte_order", "scale", "offset", "unit"):
                self.assertIsNone(entry[field])
        self.assertEqual([], validated["unsupported"])

    def test_explicit_function_code_in_evidence_corrects_the_function(self):
        digital_input = {
            "id": "input1",
            "name": "Entrada digital 1",
            "function": "read_coils",
            "address": 0,
            "count": 1,
            "type": None,
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
        }
        digital_input["evidence"][0]["excerpt"] = (
            "Input channels 1~8 statuses | Read | 0x02")

        normalized = _normalize_provider_proposal(proposal(digital_input))

        self.assertEqual(
            "read_discrete_inputs", normalized["reads"][0]["function"])

    def test_coil_table_coordinates_resolve_the_bit_address(self):
        digital_input = {
            "id": "input1",
            "name": "Validez de entrada digital 1",
            "function": "read_coils",
            "address": None,
            "count": 1,
            "type": None,
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
        }
        digital_input["evidence"][0]["excerpt"] = (
            "Base Unit Digital Input 1 Validity | 2400 | 0 | 38400")
        value = proposal(digital_input)
        value["pending"] = [{
            "scope": "read",
            "field": "reads.input1.address",
            "question": "¿Cuál es la dirección Modbus?",
            "reason": "No consta.",
            "can_research_web": False,
            "web_query": None,
            "evidence": [evidence()],
        }]

        normalized = _normalize_provider_proposal(
            value, {"operation": "refine", "current": {}})

        self.assertEqual(38400, normalized["reads"][0]["address"])
        self.assertEqual([], normalized["pending"])

    def test_persistent_configuration_write_is_not_operational(self):
        setting = {
            "id": "w000001",
            "name": "Offset de usuario para temperatura",
            "function": "write_single_register",
            "address": 12,
            "count": 1,
            "type": "int16",
            "byte_order": None,
            "scale": 0.1,
            "offset": None,
            "unit": "°C",
            "evidence": [evidence()],
            "purpose": "operational",
        }
        setting["evidence"][0]["excerpt"] = (
            "User offset for temperature | Register 12 | R/W")
        value = proposal()
        value["writes"] = [setting]

        normalized = _normalize_provider_proposal(value)

        self.assertEqual([], normalized["writes"])
        self.assertEqual("other", normalized["unsupported"][-1]["category"])
        self.assertIn(
            "ajuste persistente de configuración",
            normalized["unsupported"][-1]["reason"],
        )

    def test_unexpected_script_is_removed_from_user_visible_text(self):
        setting = read_entry("r000001", "Offset de usuario para la温度", 12)
        setting["unit"] = "unidad工程"
        setting["evidence"][0]["excerpt"] = "User offset for temperature"
        value = proposal(setting)
        value["device"]["name"] = "Transmisor de温度和"

        normalized = _normalize_provider_proposal(value)

        self.assertEqual("Meter 100", normalized["device"]["name"])
        self.assertEqual(
            "Offset usuario temperatura",
            normalized["reads"][0]["name"],
        )
        self.assertIsNone(normalized["reads"][0]["unit"])

    def test_ambiguous_function_codes_in_evidence_do_not_override_the_model(self):
        digital_output = {
            "id": "output1",
            "name": "Salida digital 1",
            "function": "write_single_coil",
            "address": 0,
            "count": 1,
            "type": None,
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
            "purpose": "operational",
        }
        digital_output["evidence"][0]["excerpt"] = (
            "Function code 0x05 or function code 0x0F")
        value = proposal()
        value["writes"] = [digital_output]

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(
            "write_single_coil", normalized["writes"][0]["function"])

    def test_explicit_mixed_access_expands_a_missing_read_operation(self):
        digital_output = {
            "id": "output1",
            "name": "Salida digital 1",
            "function": "write_single_coil",
            "address": 0,
            "count": 1,
            "type": None,
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
            "purpose": "operational",
        }
        digital_output["evidence"][0]["excerpt"] = (
            "Output channels | Read/Write | 0x01, 0x05, 0x0F")
        value = proposal()
        value["writes"] = [digital_output]
        envelope = {
            "proposal": value,
            "coverage": [{
                "section_id": "measurements",
                "status": "complete",
                "read_ids": [],
                "write_ids": ["output1"],
                "reason": None,
            }],
            "summary": "ok",
        }

        _expand_evidenced_read_write_operations(envelope)

        self.assertEqual(1, len(value["reads"]))
        self.assertEqual("read_coils", value["reads"][0]["function"])
        self.assertEqual(0, value["reads"][0]["address"])
        self.assertNotIn("purpose", value["reads"][0])
        self.assertEqual(
            [value["reads"][0]["id"]],
            envelope["coverage"][0]["read_ids"],
        )

    def test_rtu_frame_canonicalizes_and_splits_a_bit_channel_range(self):
        bit_range = {
            "id": "r000001",
            "name": "Entradas digitales 1~8",
            "function": "read_input_registers",
            "address": 0,
            "count": 8,
            "type": None,
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
        }
        bit_range["evidence"][0]["excerpt"] = (
            "Solicitud: 01 02 00 00 00 08 79 CC; "
            "address 0x0000-0x0007")
        value = proposal(bit_range)
        envelope = {
            "proposal": value,
            "coverage": [coverage("measurements", read_ids=["r000001"])],
            "summary": "ok",
        }

        _expand_evidenced_read_write_operations(envelope)

        self.assertEqual(8, len(value["reads"]))
        self.assertEqual(
            list(range(8)), [entry["address"] for entry in value["reads"]])
        self.assertTrue(all(
            entry["function"] == "read_discrete_inputs"
            and entry["count"] == 1
            for entry in value["reads"]
        ))
        self.assertEqual(8, len(envelope["coverage"][0]["read_ids"]))

    def test_mixed_access_does_not_duplicate_an_existing_read_operation(self):
        existing = {
            "id": "output1_read",
            "name": "Estado de salida digital 1",
            "function": "read_coils",
            "address": 0,
            "count": 1,
            "type": None,
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
        }
        output = copy.deepcopy(existing)
        output.update({
            "id": "output1_write",
            "function": "write_single_coil",
            "purpose": "operational",
        })
        output["evidence"][0]["excerpt"] = (
            "Output channels | Read/Write | 0x01, 0x05")
        value = proposal(existing)
        value["writes"] = [output]
        envelope = {"proposal": value, "coverage": [], "summary": "ok"}

        _expand_evidenced_read_write_operations(envelope)

        self.assertEqual(1, len(value["reads"]))

    def test_mixed_access_expands_after_correcting_an_unrelated_read(self):
        wrong_input = {
            "id": "input1",
            "name": "Entrada digital 1",
            "function": "read_coils",
            "address": 0,
            "count": 1,
            "type": None,
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
        }
        wrong_input["evidence"][0]["excerpt"] = (
            "Input channels | Read | 0x02")
        output = copy.deepcopy(wrong_input)
        output.update({
            "id": "output1",
            "name": "Salida digital 1",
            "function": "write_single_coil",
            "purpose": "operational",
        })
        output["evidence"][0]["excerpt"] = (
            "Output channels | Read/Write | 0x01, 0x05")
        value = proposal(wrong_input)
        value["writes"] = [output]
        envelope = {"proposal": value, "coverage": [], "summary": "ok"}

        _expand_evidenced_read_write_operations(envelope)

        self.assertEqual(2, len(value["reads"]))
        self.assertEqual(
            {"read_coils", "read_discrete_inputs"},
            {entry["function"] for entry in value["reads"]},
        )

    def test_mixed_access_expands_every_evidenced_output_in_a_large_catalog(self):
        value = proposal()
        value["reads"] = []
        value["writes"] = []
        for channel in range(8):
            input_entry = {
                "id": f"input{channel + 1}",
                "name": f"Entrada digital {channel + 1}",
                "function": "read_coils",
                "address": channel,
                "count": 1,
                "type": None,
                "byte_order": None,
                "scale": None,
                "offset": None,
                "unit": None,
                "evidence": [evidence()],
            }
            input_entry["evidence"][0]["excerpt"] = (
                "Input channels 1~8 statuses | Read | 0x02")
            value["reads"].append(input_entry)

            mode_entry = copy.deepcopy(input_entry)
            mode_entry.update({
                "id": f"mode{channel + 1}",
                "name": f"Modo de control {channel + 1}",
                "function": "read_holding_registers",
                "address": 0x1000 + channel,
                "type": "uint16",
            })
            mode_entry["evidence"][0]["excerpt"] = (
                "Control mode register | Read/Write | 0x03, 0x06")
            value["reads"].append(mode_entry)

            output_entry = copy.deepcopy(input_entry)
            output_entry.update({
                "id": f"output{channel + 1}",
                "name": f"Salida digital {channel + 1}",
                "function": "write_single_coil",
                "purpose": "operational",
            })
            output_entry["evidence"][0]["excerpt"] = (
                "Output channels 1~8 | Read/Write | 0x01, 0x05, 0x0F")
            value["writes"].append(output_entry)
        envelope = {"proposal": value, "coverage": [], "summary": "ok"}

        _expand_evidenced_read_write_operations(envelope)

        self.assertEqual(24, len(value["reads"]))
        self.assertEqual(
            list(range(8)),
            sorted(
                entry["address"] for entry in value["reads"]
                if entry["function"] == "read_coils"
            ),
        )
        self.assertTrue(all(
            entry["function"] == "read_discrete_inputs"
            for entry in value["reads"][:16:2]
        ))

    def test_explained_discards_keep_the_valid_catalog(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
        ))
        voltage = read_entry("voltage", "Tensión", 100)
        value = proposal(voltage)
        value["unsupported"] = [{
            "category": "data_shape",
            "summary": "Energía acumulada de 64 bits no se incluyó",
            "reason": "El formulario admite valores escalares de hasta 32 bits.",
            "evidence": [evidence()],
        }]
        proposed = validate_proposal(value)
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage(
                    "measurements",
                    read_ids=["voltage", "energy64"],
                ),
            ),
            found,
            proposed,
        )
        self.assertEqual(
            [],
            extraction_quality_issues(
                found,
                proposed,
                checked,
                discarded_entries=[{
                    "collection": "reads",
                    "id": "energy64",
                    "category": "data_shape",
                    "reason": "El formulario admite valores escalares de hasta 32 bits.",
                }],
            ),
        )

    def test_unexplained_discards_remain_insufficient(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
        ))
        voltage = read_entry("voltage", "Tensión", 100)
        proposed = validate_proposal(proposal(voltage))
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage("measurements", read_ids=["voltage"]),
            ),
            found,
            proposed,
        )
        self.assertIn(
            "la normalización descartó parámetros sin explicar el motivo",
            extraction_quality_issues(
                found,
                proposed,
                checked,
                discarded_entries=[{
                    "collection": "reads",
                    "id": "energy64",
                    "category": "data_shape",
                    "reason": "El formulario admite valores escalares de hasta 32 bits.",
                }],
            ),
        )

    def test_full_read_catalog_must_explain_the_limit(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
        ))
        reads = [
            read_entry(f"r{index:06d}", f"Medida {index}", index * 2)
            for index in range(1, 33)
        ]
        proposed = validate_proposal(proposal(*reads))
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage(
                    "measurements",
                    read_ids=[entry["id"] for entry in reads],
                ),
            ),
            found,
            proposed,
        )
        self.assertIn(
            "la propuesta alcanzó el límite de lecturas sin declarar "
            "qué parámetros quedaron fuera",
            extraction_quality_issues(found, proposed, checked),
        )

        value = proposal(*reads)
        value["unsupported"] = [{
            "category": "catalog_limit",
            "summary": "Mediciones secundarias omitidas",
            "reason": "Se alcanzó el límite del catálogo.",
            "evidence": [evidence()],
        }]
        proposed = validate_proposal(value)
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage(
                    "measurements",
                    read_ids=[entry["id"] for entry in reads],
                ),
            ),
            found,
            proposed,
        )
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_normalization_preserves_contract_limit_and_wide_rows(self):
        measurements = section("measurements", "measurement", page=40)
        measurements["evidence"].append({
            "source_id": "manual-1",
            "page": 43,
            "section": "Table 3-6",
            "excerpt": "801 4 Active Energy Import Tariff 1 Double Wh R",
        })
        found = validate_discovery(discovery(measurements))
        reads = [
            read_entry(f"r{index:06d}", f"Medida {index}", index * 2,
                       page=40)
            for index in range(1, 33)
        ]
        normalized = normalize_extraction_coverage(
            extraction(
                proposal(*reads),
                coverage(
                    "measurements",
                    read_ids=[entry["id"] for entry in reads],
                ),
            ),
            found,
        )
        categories = {
            item["category"]
            for item in normalized["proposal"]["unsupported"]
        }

        self.assertIn("catalog_limit", categories)
        self.assertIn("data_shape", categories)

    def test_metadata_coverage_does_not_hide_an_empty_operational_section(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement", estimated_parameters=4),
            section("device_info", "metadata", estimated_parameters=2),
        ))
        serial = read_entry(
            "serial", "Número de serie", 200,
            value_type="uint16", count=1, byte_order=None,
        )
        firmware = read_entry(
            "fw_ver", "Versión firmware", 201,
            value_type="uint16", count=1, byte_order=None,
        )
        proposed = validate_proposal(proposal(serial, firmware))
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage("measurements"),
                coverage("device_info", read_ids=["serial", "fw_ver"]),
            ),
            found,
            proposed,
        )
        self.assertIn(
            "la sección operativa measurements no produjo parámetros",
            extraction_quality_issues(found, proposed, checked),
        )

    def test_explained_empty_control_section_is_preserved_as_unsupported(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement", page=12),
            section(
                "controls",
                "operational_control",
                access="write",
                page=20,
            ),
        ))
        voltage = read_entry("voltage", "Tensión", 100, page=12)
        reason = (
            "La operación requiere configurar varios registros juntos y no "
            "puede cargarse como una escritura independiente."
        )
        normalized = normalize_extraction_coverage(
            extraction(
                proposal(voltage),
                coverage("measurements", read_ids=["voltage"]),
                coverage("controls", reason=reason),
            ),
            found,
        )
        proposed = validate_proposal(normalized["proposal"])
        checked = validate_extraction_envelope(normalized, found, proposed)

        controls = checked["coverage"][1]
        self.assertEqual("no_applicable", controls["status"])
        self.assertTrue(any(
            item.get("reason") == reason
            for item in proposed["unsupported"]
        ))
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_reconstructed_entries_override_no_applicable_coverage(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement", page=12),
        ))
        voltage = read_entry("voltage", "Tensión", 100, page=12)
        normalized = normalize_extraction_coverage(
            extraction(
                proposal(voltage),
                coverage(
                    "measurements",
                    status="no_applicable",
                    reason="El modelo no reconoció la tabla como aplicable.",
                ),
            ),
            found,
        )
        proposed = validate_proposal(normalized["proposal"])
        checked = validate_extraction_envelope(normalized, found, proposed)

        self.assertEqual("complete", checked["coverage"][0]["status"])
        self.assertEqual(["r000001"], checked["coverage"][0]["read_ids"])
        self.assertIsNone(checked["coverage"][0]["reason"])
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_unique_declared_section_resolves_overlapping_evidence_pages(self):
        output_map = section("output_map", "operational_control", page=25)
        output_map["page_end"] = 25
        found = validate_discovery(discovery(
            output_map,
            section("command_one", "operational_control", page=26),
            section("command_all", "operational_control", page=26),
        ))
        output = read_entry("output1", "Estado de salida 1", 0, page=26)
        normalized = normalize_extraction_coverage(
            extraction(
                proposal(output),
                coverage("output_map", read_ids=["output1"]),
                coverage(
                    "command_one", status="no_applicable",
                    reason="La trama repite el parámetro del mapa."
                ),
                coverage(
                    "command_all", status="no_applicable",
                    reason="La trama corresponde al control global."
                ),
            ),
            found,
        )
        proposed = validate_proposal(normalized["proposal"])
        checked = validate_extraction_envelope(normalized, found, proposed)

        self.assertEqual(["r000001"], checked["coverage"][0]["read_ids"])
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_exact_section_evidence_resolves_duplicate_coverage(self):
        diagnostics = section("diagnostics", "status", page=45)
        diagnostics["evidence"] = [{
            "source_id": "manual-1",
            "page": 45,
            "section": "Table 3-8",
            "excerpt": "Device status and diagnostics",
        }]
        limits = section("limits", "status", page=45)
        limits["evidence"] = [{
            "source_id": "manual-1",
            "page": 45,
            "section": "Table 3-9",
            "excerpt": "Register 203: Limit Violations",
        }]
        found = validate_discovery(discovery(diagnostics, limits))
        violation = read_entry(
            "violation", "Violaciones de límite", 203,
            value_type="uint32", page=45,
        )
        violation["evidence"] = [{
            "source_id": "manual-1",
            "page": 45,
            "section": "Table 3-9",
            "excerpt": "Register 203: Limit Violations",
        }]
        normalized = normalize_extraction_coverage(
            extraction(
                proposal(violation),
                coverage("diagnostics", read_ids=["violation"]),
                coverage("limits", read_ids=["violation"]),
            ),
            found,
        )
        proposed = validate_proposal(normalized["proposal"])
        checked = validate_extraction_envelope(normalized, found, proposed)
        by_section = {
            item["section_id"]: item for item in checked["coverage"]
        }

        self.assertEqual([], by_section["diagnostics"]["read_ids"])
        self.assertEqual(["r000001"], by_section["limits"]["read_ids"])

    def test_explained_complete_section_without_entries_becomes_no_applicable(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement", page=12),
            section("duplicated", "measurement", page=39),
        ))
        voltage = read_entry("voltage", "Tensión", 100, page=12)
        normalized = normalize_extraction_coverage(
            extraction(
                proposal(voltage),
                coverage("measurements", read_ids=["voltage"]),
                coverage(
                    "duplicated",
                    status="complete",
                    reason=(
                        "La sección repite magnitudes ya documentadas y no "
                        "produce otra lectura compatible."
                    ),
                ),
            ),
            found,
        )
        proposed = validate_proposal(normalized["proposal"])
        checked = validate_extraction_envelope(normalized, found, proposed)

        self.assertEqual("no_applicable", checked["coverage"][1]["status"])
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_misassigned_control_entries_leave_an_explained_exclusion(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement", page=12),
            section(
                "controls",
                "operational_control",
                access="write",
                page=20,
            ),
        ))
        voltage = read_entry("voltage", "Tensión", 100, page=12)
        normalized = normalize_extraction_coverage(
            extraction(
                proposal(voltage),
                coverage("measurements"),
                coverage("controls", read_ids=["voltage"]),
            ),
            found,
        )
        proposed = validate_proposal(normalized["proposal"])
        checked = validate_extraction_envelope(normalized, found, proposed)

        controls = checked["coverage"][1]
        self.assertEqual("no_applicable", controls["status"])
        self.assertIn("evidencia en otras secciones", controls["reason"])
        self.assertTrue(any(
            "evidencia en otras secciones" in item.get("reason", "")
            for item in proposed["unsupported"]
        ))
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_estimated_parameters_do_not_block_a_valid_subset(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement", estimated_parameters=3),
        ))
        voltage = read_entry("voltage", "Tensión", 100)
        proposed = validate_proposal(proposal(voltage))
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage("measurements", read_ids=["voltage"]),
            ),
            found,
            proposed,
        )
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_no_applicable_operational_section_requires_an_explanation(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
            section("alarms", "status", estimated_parameters=20),
        ))
        voltage = read_entry("voltage", "Tensión", 100)
        proposed = validate_proposal(proposal(voltage))
        with self.assertRaises(CatalogValidationError) as raised:
            validate_extraction_envelope(
                extraction(
                    proposed,
                    coverage("measurements", read_ids=["voltage"]),
                    coverage("alarms", status="no_applicable"),
                ),
                found,
                proposed,
            )
        self.assertIn(
            "$.coverage[1].reason: obligatorio cuando status es no_applicable",
            raised.exception.errors,
        )

        explained = validate_extraction_envelope(
            extraction(
                proposed,
                coverage("measurements", read_ids=["voltage"]),
                coverage(
                    "alarms",
                    status="no_applicable",
                    reason="La sección describe bloques no escalares no representables.",
                ),
            ),
            found,
            proposed,
        )
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, explained))

    def test_each_kept_entry_has_exactly_one_coverage_section(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
            section("summary", "status"),
        ))
        voltage = read_entry("voltage", "Tensión", 100)
        proposed = validate_proposal(proposal(voltage))

        with self.assertRaises(CatalogValidationError) as missing:
            validate_extraction_envelope(
                extraction(
                    proposed,
                    coverage(
                        "measurements",
                        status="no_applicable",
                        reason="No produjo otro valor compatible.",
                    ),
                    coverage(
                        "summary",
                        status="no_applicable",
                        reason="No produjo otro valor compatible.",
                    ),
                ),
                found,
                proposed,
            )
        self.assertIn(
            "$.coverage: la lectura voltage no tiene sección de cobertura",
            missing.exception.errors,
        )

        with self.assertRaises(CatalogValidationError) as duplicated:
            validate_extraction_envelope(
                extraction(
                    proposed,
                    coverage("measurements", read_ids=["voltage"]),
                    coverage("summary", read_ids=["voltage"]),
                ),
                found,
                proposed,
            )
        self.assertIn(
            "$.coverage: la lectura voltage pertenece a varias secciones",
            duplicated.exception.errors,
        )

    def test_unreported_orphan_coverage_reference_remains_insufficient(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
        ))
        voltage = read_entry("voltage", "Tensión", 100)
        value = proposal(voltage)
        value["unsupported"] = [{
            "category": "data_shape",
            "summary": "Energía acumulada de 64 bits no se incluyó",
            "reason": "El formulario admite valores escalares de hasta 32 bits.",
            "evidence": [evidence()],
        }]
        proposed = validate_proposal(value)
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage(
                    "measurements",
                    read_ids=["voltage", "energy64", "unknown"],
                ),
            ),
            found,
            proposed,
        )
        self.assertIn(
            "la sección measurements referencia parámetros descartados",
            extraction_quality_issues(
                found,
                proposed,
                checked,
                discarded_entries=[{
                    "collection": "reads",
                    "id": "energy64",
                    "category": "data_shape",
                    "reason": "El formulario admite valores escalares de hasta 32 bits.",
                }],
            ),
        )

    def test_ambiguous_duplicate_identifier_remains_insufficient(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
        ))
        voltage = read_entry("voltage", "Tensión", 100)
        duplicate_reason = (
            "El identificador ya pertenece a otro parámetro de la propuesta.")
        value = proposal(voltage)
        value["unsupported"] = [{
            "category": "other",
            "summary": "Tensión duplicada no se incluyó",
            "reason": duplicate_reason,
            "evidence": [evidence()],
        }]
        proposed = validate_proposal(value)
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage("measurements", read_ids=["voltage"]),
            ),
            found,
            proposed,
        )
        self.assertIn(
            "la normalización detectó identificadores duplicados o ambiguos",
            extraction_quality_issues(
                found,
                proposed,
                checked,
                discarded_entries=[{
                    "collection": "reads",
                    "id": "voltage",
                    "category": "other",
                    "reason": duplicate_reason,
                }],
            ),
        )

    def test_fully_empty_catalog_still_blocks_when_discards_are_explained(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
        ))
        value = proposal()
        value["unsupported"] = [{
            "category": "data_shape",
            "summary": "Energía acumulada de 64 bits no se incluyó",
            "reason": "El formulario admite valores escalares de hasta 32 bits.",
            "evidence": [evidence()],
        }]
        proposed = validate_proposal(value)
        checked = validate_extraction_envelope(
            extraction(
                proposed,
                coverage(
                    "measurements",
                    status="no_applicable",
                    reason="La sección solo contiene valores de 64 bits.",
                ),
            ),
            found,
            proposed,
        )
        self.assertIn(
            "la extracción no produjo lecturas ni escrituras aplicables",
            extraction_quality_issues(
                found,
                proposed,
                checked,
                discarded_entries=[{
                    "collection": "reads",
                    "id": "energy64",
                    "category": "data_shape",
                    "reason": "El formulario admite valores escalares de hasta 32 bits.",
                }],
            ),
        )


class StagedProviderTests(unittest.TestCase):
    def setUp(self):
        self._manual_upload = patch(
            "modbus_ai_provider._upload_provider_file",
            return_value="file-manual-test",
        )
        self._manual_delete = patch(
            "modbus_ai_provider._delete_provider_file")
        self._manual_upload.start()
        self._manual_delete.start()
        self.addCleanup(self._manual_delete.stop)
        self.addCleanup(self._manual_upload.stop)

    def _request(self, responses, **request_kwargs):
        request = validated_request(**request_kwargs)
        with patch("modbus_ai_provider.post_responses",
                   side_effect=responses) as mocked, patch(
                   "modbus_ai_provider._upload_provider_file",
                   return_value="file-manual-test"), patch(
                   "modbus_ai_provider._delete_provider_file"):
            result = request_proposal(
                CONFIG,
                "offline-test-key",
                request,
                security_mode="production",
            )
        return result, mocked

    def test_discovery_returns_targets_and_sections_without_extracting(self):
        found = discovery(
            section("measurements", "measurement", estimated_parameters=2),
        )
        result, mocked = self._request([provider_response(found)])
        self.assertEqual(1, mocked.call_count)
        self.assertEqual(
            "modulinkr_modbus_discovery",
            mocked.call_args.args[2]["text"]["format"]["name"],
        )
        self.assertEqual("meter100", result["discovery"]["targets"][0]["id"])
        self.assertEqual("sec01", result["discovery"]["sections"][0]["id"])

    def test_discovery_reserves_output_for_reasoning_and_structured_json(self):
        request = validated_request(kind="identity")
        payload = build_discovery_payload(request, "gpt-5.4-mini")

        self.assertEqual(DISCOVERY_MAX_OUTPUT_TOKENS, payload["max_output_tokens"])
        self.assertGreaterEqual(payload["max_output_tokens"], 18000)

        prepared = prepare_provider_payload({
            **CONFIG,
            "provider": "openai",
            "model": "gpt-5.4-mini",
        }, payload)
        self.assertEqual({"effort": "low"}, prepared["reasoning"])

    def test_incomplete_provider_response_records_the_limit_reason(self):
        request = validated_request(kind="identity")
        response = {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"output_tokens": 8000},
            "output": [],
        }
        with patch("modbus_ai_provider.post_responses", return_value=response):
            with self.assertRaises(ProviderCallError) as caught:
                request_proposal(
                    {
                        **CONFIG,
                        "provider": "openai",
                        "model": "gpt-5.4-mini",
                    },
                    "offline-test-key",
                    request,
                    security_mode="production",
                )

        self.assertEqual(
            "status=incomplete; reason=max_output_tokens; output_tokens=8000",
            caught.exception.technical_detail,
        )

    def test_openai_gpt54_raises_reasoning_only_for_web_search(self):
        found = discovery(section("measurements", "measurement"))
        request = validated_request()
        openai_config = {
            **CONFIG,
            "provider": "openai",
            "model": "gpt-5.4-mini",
        }
        with patch("modbus_ai_provider.post_responses",
                   return_value=provider_response(found)) as mocked:
            request_proposal(
                openai_config, "offline-test-key", request,
                security_mode="production",
            )
        self.assertEqual(
            {"effort": "low"}, mocked.call_args.args[2]["reasoning"])

        prepared = prepare_provider_payload(openai_config, {
            "tools": [{"type": "web_search"}],
        })
        self.assertEqual({"effort": "medium"}, prepared["reasoning"])

        compatible_config = {
            **openai_config,
            "provider": "openai_compatible",
            "base_url": "https://provider.example/v1",
        }
        with patch("modbus_ai_provider.post_responses",
                   return_value=provider_response(found)) as mocked:
            request_proposal(
                compatible_config, "offline-test-key", request,
                security_mode="production",
            )
        self.assertNotIn("reasoning", mocked.call_args.args[2])

    def test_openai_web_search_uses_streaming_and_long_timeout(self):
        found = discovery(
            section("measurements", "measurement"), kind="web")
        request = validated_request(kind="identity")
        openai_config = {
            **CONFIG,
            "provider": "openai",
            "model": "gpt-5.4-mini",
        }

        with patch("modbus_ai_provider.post_responses",
                   return_value=provider_response(found)) as posted:
            request_proposal(
                openai_config, "offline-test-key", request,
                security_mode="production",
            )

        self.assertEqual(600.0, posted.call_args.kwargs["timeout_s"])
        self.assertTrue(posted.call_args.kwargs["stream"])

    def test_remote_technical_file_is_relayed_to_code_interpreter_and_deleted(self):
        found = discovery(
            section("measurements", "measurement"), kind="web")
        found["sources"][0].update({
            "title": "meter-register-list.xlsx",
            "url": "https://files.example.test/meter-register-list.xlsx",
        })
        extracted_proposal = proposal(
            read_entry("r000001", "Tensión", 100, source_id="manual-1"),
            kind="web",
        )
        extracted_proposal["sources"][0].update({
            "title": "meter-register-list.xlsx",
            "url": "https://files.example.test/meter-register-list.xlsx",
        })
        extracted = extraction(
            extracted_proposal,
            coverage("measurements", read_ids=["r000001"]),
        )
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"],
        )

        with patch(
                "modbus_ai_provider._download_public_technical_file",
                return_value=b"xlsx bytes") as downloaded, patch(
                "modbus_ai_provider._upload_provider_file",
                return_value="file-test123") as uploaded, patch(
                "modbus_ai_provider._delete_provider_file") as deleted, patch(
                "modbus_ai_provider.post_responses",
                return_value=provider_response(extracted)) as posted:
            result = request_proposal(
                CONFIG, "offline-test-key", request,
                security_mode="production")

        downloaded.assert_called_once_with(
            "https://files.example.test/meter-register-list.xlsx",
            "meter-register-list.xlsx",
        )
        self.assertEqual(b"xlsx bytes", uploaded.call_args.args[3])
        deleted.assert_called_once_with(
            CONFIG["base_url"], "offline-test-key", "file-test123",
            allow_loopback=False,
        )
        payload = posted.call_args.args[2]
        self.assertEqual(
            ["file-test123"], payload["tools"][0]["container"]["file_ids"])
        self.assertEqual(600.0, posted.call_args.kwargs["timeout_s"])
        self.assertTrue(posted.call_args.kwargs["stream"])
        content = payload["input"][1]["content"]
        self.assertFalse(any(item.get("type") == "input_file" for item in content))
        self.assertEqual(1, len(result["proposal"]["reads"]))

    def test_remote_pdf_is_relayed_with_a_pdf_filename(self):
        found = discovery(
            section("measurements", "measurement"), kind="web")
        found["sources"][0].update({
            "title": "Device instruction manual",
            "url": "https://files.example.test/device-manual.pdf",
        })
        extracted_proposal = proposal(
            read_entry("r000001", "Temperatura", 1, source_id="manual-1"),
            kind="web",
        )
        extracted_proposal["sources"][0].update({
            "title": "Device instruction manual",
            "url": "https://files.example.test/device-manual.pdf",
        })
        extracted = extraction(
            extracted_proposal,
            coverage("measurements", read_ids=["r000001"]),
        )
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"],
        )

        with patch(
                "modbus_ai_provider._download_public_technical_file",
                return_value=b"%PDF-1.7 test") as downloaded, patch(
                "modbus_ai_provider._upload_provider_file",
                return_value="file-pdf123") as uploaded, patch(
                "modbus_ai_provider._delete_provider_file"), patch(
                "modbus_ai_provider.post_responses",
                return_value=provider_response(extracted)):
            result = request_proposal(
                CONFIG, "offline-test-key", request,
                security_mode="production")

        downloaded.assert_called_once_with(
            "https://files.example.test/device-manual.pdf",
            "Device instruction manual",
        )
        self.assertEqual("device-manual.pdf", uploaded.call_args.args[2])
        self.assertEqual(1, len(result["proposal"]["reads"]))

    def test_streaming_returns_the_terminal_response_object(self):
        completed = provider_response(proposal())
        lines = [
            b"event: response.created\n",
            b'data: {"type":"response.created","response":{"status":"in_progress"}}\n',
            b"\n",
            ("data: " + json.dumps({
                "type": "response.completed",
                "response": completed,
            }) + "\n").encode("utf-8"),
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ]

        result = _response_from_sse_lines(lines)

        self.assertEqual("completed", result["status"])
        self.assertEqual(completed["output"], result["output"])

    def test_manual_is_relayed_for_discovery_and_deleted(self):
        found = discovery(section("measurements", "measurement"))
        request = validated_request()

        with patch(
                "modbus_ai_provider._upload_provider_file",
                return_value="file-manual123") as uploaded, patch(
                "modbus_ai_provider._delete_provider_file") as deleted, patch(
                "modbus_ai_provider.post_responses",
                return_value=provider_response(found)) as posted:
            result = request_proposal(
                CONFIG, "offline-test-key", request,
                security_mode="production")

        self.assertTrue(uploaded.call_args.args[3].startswith(b"%PDF-"))
        deleted.assert_called_once_with(
            CONFIG["base_url"], "offline-test-key", "file-manual123",
            allow_loopback=False,
        )
        payload = posted.call_args.args[2]
        self.assertEqual(
            ["file-manual123"],
            payload["tools"][0]["container"]["file_ids"],
        )
        self.assertFalse(any(
            item.get("type") == "input_file"
            for item in payload["input"][1]["content"]
        ))
        self.assertEqual("Meter 100", result["discovery"]["identity"]["model"])

    def test_manual_is_relayed_for_extraction_and_deleted(self):
        found = discovery(section("measurements", "measurement"))
        extracted = extraction(
            proposal(read_entry("r000001", "Tensión", 100)),
            coverage("measurements", read_ids=["r000001"]),
        )
        request = validated_request(
            operation="extract", found=found,
            selected_sections=["measurements"],
        )

        with patch(
                "modbus_ai_provider._upload_provider_file",
                return_value="file-manual123") as uploaded, patch(
                "modbus_ai_provider._delete_provider_file") as deleted, patch(
                "modbus_ai_provider.post_responses",
                return_value=provider_response(extracted)) as posted:
            result = request_proposal(
                CONFIG, "offline-test-key", request,
                security_mode="production")

        self.assertTrue(uploaded.call_args.args[3].startswith(b"%PDF-"))
        deleted.assert_called_once_with(
            CONFIG["base_url"], "offline-test-key", "file-manual123",
            allow_loopback=False,
        )
        payload = posted.call_args.args[2]
        self.assertEqual(
            ["file-manual123"],
            payload["tools"][0]["container"]["file_ids"],
        )
        self.assertFalse(any(
            item.get("type") == "input_file"
            for item in payload["input"][1]["content"]
        ))
        self.assertEqual(1, len(result["proposal"]["reads"]))

    def test_remote_file_is_deleted_when_model_call_fails(self):
        found = discovery(
            section("measurements", "measurement"), kind="web")
        found["sources"][0].update({
            "title": "meter-register-list.xlsx",
            "url": "https://files.example.test/meter-register-list.xlsx",
        })
        request = validated_request(
            kind="identity", operation="extract", found=found,
            selected_sections=["measurements"],
        )

        with patch(
                "modbus_ai_provider._download_public_technical_file",
                return_value=b"xlsx bytes"), patch(
                "modbus_ai_provider._upload_provider_file",
                return_value="file-test123"), patch(
                "modbus_ai_provider._delete_provider_file") as deleted, patch(
                "modbus_ai_provider.post_responses",
                side_effect=ProviderCallError("fallo remoto")):
            with self.assertRaisesRegex(ProviderCallError, "fallo remoto"):
                request_proposal(
                    CONFIG, "offline-test-key", request,
                    security_mode="production")

        deleted.assert_called_once_with(
            CONFIG["base_url"], "offline-test-key", "file-test123",
            allow_loopback=False,
        )

    def test_duplicate_model_section_ids_are_replaced_without_recovery(self):
        found = discovery(
            section("duplicate", "measurement"),
            section("duplicate", "status", page=20),
        )

        result, mocked = self._request([provider_response(found)])

        self.assertEqual(1, mocked.call_count)
        self.assertEqual(
            ["sec01", "sec02"],
            [item["id"] for item in result["discovery"]["sections"]],
        )

    def test_extraction_uses_only_selected_sections(self):
        found = discovery(
            section("measurements", "measurement"),
            section("metadata", "metadata"),
        )
        voltage = read_entry("voltage", "Tensión", 100)
        extracted = extraction(
            proposal(voltage),
            coverage("measurements", read_ids=["voltage"]),
        )
        result, mocked = self._request(
            [provider_response(extracted)], operation="extract", found=found,
            selected_sections=["measurements"],
        )
        prompt_text = mocked.call_args.args[2]["input"][1]["content"][0]["text"]
        prompt = json.loads(prompt_text.split("\n", 1)[1])
        self.assertEqual(["measurements"], prompt["selected_sections"])
        self.assertEqual(
            ["measurements"],
            [item["id"] for item in prompt["discovery"]["sections"]],
        )
        self.assertEqual(
            ["measurement"],
            prompt["extraction_scope"]["selected_categories"],
        )
        self.assertEqual(
            ["metadata"],
            prompt["extraction_scope"]["excluded_categories"],
        )
        self.assertEqual(
            ["metadata"],
            [item["id"] for item in
             prompt["extraction_scope"]["excluded_sections"]],
        )
        self.assertEqual("r000001", result["proposal"]["reads"][0]["id"])

    def test_extraction_prompt_propagates_global_function_codes(self):
        self.assertIn(
            "tanto 0x03 como 0x04, usa read_holding_registers",
            EXTRACTION_SYSTEM_PROMPT,
        )
        self.assertIn("category data_shape", EXTRACTION_SYSTEM_PROMPT)
        self.assertIn(
            "Number of registers con valor 0",
            EXTRACTION_SYSTEM_PROMPT,
        )
        self.assertIn(
            "read_coils o read_discrete_inputs",
            EXTRACTION_SYSTEM_PROMPT,
        )
        self.assertIn(
            "no llenes el límite recorriendo simplemente las filas",
            EXTRACTION_SYSTEM_PROMPT,
        )
        self.assertIn("potencia activa total", EXTRACTION_SYSTEM_PROMPT)
        self.assertIn("category catalog_limit", EXTRACTION_SYSTEM_PROMPT)
        self.assertIn(
            "no inspecciones las tablas mediante impresiones parciales",
            EXTRACTION_SYSTEM_PROMPT,
        )
        self.assertIn(
            "reads debe contener exactamente min(total de lecturas compatibles, 32)",
            EXTRACTION_SYSTEM_PROMPT,
        )
        self.assertIn(
            "No elijas un conjunto \"limitado\"",
            EXTRACTION_SYSTEM_PROMPT,
        )

    def test_discovery_prompt_requires_evidence_for_global_conventions(self):
        self.assertIn(
            "incluye en evidence un extracto que cite directamente",
            DISCOVERY_SYSTEM_PROMPT,
        )
        self.assertIn(
            "Las citas de la primera y la última fila no sustituyen",
            DISCOVERY_SYSTEM_PROMPT,
        )
        self.assertIn(
            "abre obligatoriamente mediante open_page",
            DISCOVERY_SYSTEM_PROMPT,
        )

    def test_extract_rejects_identity_from_a_different_family_target(self):
        found = discovery(section("common", "measurement"))
        found["document_scope"] = "product_family"
        second = target("meter200")
        second["label"] = "Example Instruments Meter 200"
        second["model"] = "Meter 200"
        found["targets"].append(second)
        found["sections"][0]["target_ids"] = ["meter100", "meter200"]
        body = request_body(
            operation="extract", found=found, selected_sections=["common"])
        body["target_id"] = "meter200"
        with self.assertRaisesRegex(
                AssistantRequestError, "confirmed_identity"):
            validate_assistant_request(body)

    def test_empty_extraction_is_not_retried_automatically(self):
        found = discovery(
            section("measurements", "measurement", estimated_parameters=1),
        )
        empty = extraction(
            proposal(),
            coverage("measurements"),
            summary="No se extrajeron parámetros.",
        )
        request = validated_request(operation="extract", found=found)
        with patch("modbus_ai_provider.post_responses",
                   side_effect=[provider_response(empty)]) as mocked:
            with self.assertRaises(ProviderCallError):
                request_proposal(
                    CONFIG, "offline-test-key", request,
                    security_mode="production")
        self.assertEqual(1, mocked.call_count)

    def test_incomplete_discovery_is_not_retried_automatically(self):
        incomplete = discovery(
            section("measurements", "measurement"),
            coverage_complete=False,
            unreviewed=["Tabla de registros de diagnóstico"],
        )
        request = validated_request()
        with patch("modbus_ai_provider.post_responses",
                   side_effect=[provider_response(incomplete)]) as mocked:
            with self.assertRaises(ProviderCallError):
                request_proposal(
                    CONFIG, "offline-test-key", request,
                    security_mode="production")
        self.assertEqual(1, mocked.call_count)

    def test_identity_discovery_accepts_a_bounded_verified_web_catalog(self):
        partial = discovery(
            section("measurements", "measurement"),
            kind="web",
            coverage_complete=False,
            unreviewed=["No se localizó el manual completo del fabricante."],
        )
        request = validated_request(kind="identity")
        with patch("modbus_ai_provider.post_responses",
                   side_effect=[provider_response(partial)]) as mocked:
            result = request_proposal(
                CONFIG, "offline-test-key", request,
                security_mode="production")
        self.assertEqual(1, mocked.call_count)
        self.assertFalse(result["discovery"]["coverage_complete"])
        self.assertEqual(["sec01"], [
            item["id"] for item in result["discovery"]["sections"]])

    def test_identity_discovery_rejects_partial_web_without_catalog_rows(self):
        partial = discovery(
            section(
                "protocol", "communication", access="none",
                applicability="information",
            ),
            kind="web",
            coverage_complete=False,
            unreviewed=["No se localizó un mapa de registros verificable."],
        )
        request = validated_request(kind="identity")
        with patch("modbus_ai_provider.post_responses",
                   side_effect=[provider_response(partial)]) as mocked:
            with self.assertRaises(ProviderCallError):
                request_proposal(
                    CONFIG, "offline-test-key", request,
                    security_mode="production")
        self.assertEqual(1, mocked.call_count)

    def test_failed_extraction_stops_after_one_call(self):
        found = discovery(section("measurements", "measurement"))
        empty = extraction(proposal(), coverage("measurements"))
        request = validated_request(
            operation="extract", found=found,
            selected_sections=["measurements"])
        with patch("modbus_ai_provider.post_responses", side_effect=[
                provider_response(empty),
        ]) as mocked:
            with self.assertRaises(ProviderCallError):
                request_proposal(
                    CONFIG,
                    "offline-test-key",
                    request,
                    security_mode="production",
                )
        self.assertEqual(1, mocked.call_count)

    def test_extraction_repairs_unique_coverage_and_drops_unselected_entries(self):
        found = discovery(section("measurements", "measurement", page=12))
        voltage = read_entry("voltage", "Tensión", 100, page=12)
        energy = read_entry("energy", "Energía", 200, page=30)
        extracted = extraction(
            proposal(voltage, dict(voltage), energy),
            coverage("measurements"),
        )

        result, mocked = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["measurements"],
        )

        self.assertEqual(1, mocked.call_count)
        self.assertEqual(
            ["r000001"],
            [item["id"] for item in result["proposal"]["reads"]],
        )

    def test_extraction_adds_communication_information_as_context(self):
        found = discovery(
            section(
                "format", "communication", access="none",
                applicability="information", page=1,
            ),
            section("measurements", "measurement", page=5),
        )
        voltage = read_entry("voltage", "Tensión", 0, page=5)
        voltage["evidence"].append(evidence(page=1, section="Formato global"))
        extracted = extraction(
            proposal(voltage),
            coverage(
                "format", status="no_applicable",
                reason="La sección solo define el formato global.",
            ),
            coverage("measurements", read_ids=["voltage"]),
        )

        result, mocked = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["measurements"],
        )

        self.assertEqual("r000001", result["proposal"]["reads"][0]["id"])
        prompt = mocked.call_args.args[2]["input"][1]["content"][0]["text"]
        self.assertIn('"id":"format"', prompt)
        self.assertIn('"id":"measurements"', prompt)

    def test_missing_information_coverage_is_reconstructed_as_context(self):
        found = discovery(
            section(
                "format", "communication", access="none",
                applicability="information", page=1,
            ),
            section("measurements", "measurement", page=5),
        )
        voltage = read_entry("voltage", "Tensión", 0, page=5)
        voltage["evidence"].append(evidence(page=1, section="Formato global"))
        extracted = extraction(
            proposal(voltage),
            coverage("measurements", read_ids=["voltage"]),
        )
        normalized = normalize_extraction_coverage(extracted, found)

        result, _ = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["measurements"],
        )

        self.assertEqual("r000001", result["proposal"]["reads"][0]["id"])
        reconstructed = next(
            item for item in normalized["coverage"]
            if item["section_id"] == "format")
        self.assertEqual("no_applicable", reconstructed["status"])
        self.assertEqual([], reconstructed["read_ids"])

    def test_extraction_uses_declared_section_to_resolve_page_overlap(self):
        found = discovery(
            section("controls", "operational_control", page=4),
            section("measurements", "measurement", page=5),
        )
        relay = read_entry("relay", "Estado del relé", 40, page=4)
        voltage = read_entry("voltage", "Tensión", 0, page=5)
        extracted = extraction(
            proposal(relay, voltage),
            coverage("controls", read_ids=["relay"]),
            coverage("measurements", read_ids=["voltage"]),
        )

        result, mocked = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["controls", "measurements"],
        )

        self.assertEqual(1, mocked.call_count)
        self.assertEqual(
            ["r000001", "r000002"],
            [item["id"] for item in result["proposal"]["reads"]],
        )

    def test_extraction_resolves_duplicate_declaration_with_exact_evidence(self):
        found = discovery(
            section("controls", "operational_control", page=4),
            section("measurements", "measurement", page=5),
        )
        voltage = read_entry("voltage", "Tensión", 0, page=5)
        extracted = extraction(
            proposal(voltage),
            coverage("controls", read_ids=["voltage"]),
            coverage("measurements", read_ids=["voltage"]),
        )

        with patch("modbus_ai_provider.post_responses",
                   side_effect=[provider_response(extracted)]) as mocked:
            result = request_proposal(
                CONFIG,
                "offline-test-key",
                validated_request(
                    operation="extract",
                    found=found,
                    selected_sections=["controls", "measurements"],
                ),
                security_mode="production",
            )
        self.assertEqual(1, mocked.call_count)
        self.assertEqual("r000001", result["proposal"]["reads"][0]["id"])

    def test_duplicate_truncated_ids_are_replaced_and_coverage_is_rebuilt(self):
        found = discovery(
            section("energy", "measurement", page=12),
            section("realtime", "measurement", page=15),
        )
        active = read_entry(
            "r_sec03_", "Energía activa", 20480,
            value_type="uint32", byte_order=None, page=12)
        reactive = read_entry(
            "r_sec03_", "Energía reactiva", 20492,
            value_type="uint32", byte_order=None, page=12)
        voltage = read_entry(
            "r_sec03_", "Tensión", 23296,
            value_type="uint32", byte_order="ABCD", page=15)
        extracted_proposal = proposal(active, reactive, voltage)
        extracted_proposal["pending"] = [{
            "scope": "read",
            "field": "reads.r_sec03_.byte_order",
            "question": "¿Cuál es el orden de bytes?",
            "reason": "El manual no lo indica de forma explícita.",
            "can_research_web": False,
            "web_query": None,
            "evidence": [evidence(page=12)],
        }]
        extracted = extraction(
            extracted_proposal,
            coverage(
                "energy",
                read_ids=["r_sec03_", "r_sec03_", "r_sec03_"],
            ),
        )

        result, mocked = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["energy"],
        )

        self.assertEqual(1, mocked.call_count)
        self.assertEqual(
            ["Energía activa", "Energía reactiva"],
            [item["name"] for item in result["proposal"]["reads"]],
        )
        self.assertEqual(
            ["r000001", "r000002"],
            [item["id"] for item in result["proposal"]["reads"]],
        )
        self.assertEqual(
            ["reads.r000001.byte_order", "reads.r000002.byte_order"],
            [item["field"] for item in result["proposal"]["pending"]],
        )

    def test_incomplete_section_is_excluded_without_losing_complete_sections(self):
        found = discovery(
            section("energy", "measurement", page=12),
            section("settings", "operational_control", access="write", page=35),
        )
        active = read_entry("active", "Energía activa", 20480, page=12)
        extracted = extraction(
            proposal(active),
            coverage("energy", read_ids=["active"]),
            coverage(
                "settings",
                status="incomplete",
                reason="La tabla de configuración no pudo revisarse completa.",
            ),
        )

        result, mocked = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["energy", "settings"],
        )

        self.assertEqual(1, mocked.call_count)
        self.assertEqual(["r000001"], [
            item["id"] for item in result["proposal"]["reads"]])
        self.assertTrue(any(
            item["summary"] == "Sección settings no se incluyó"
            and item["reason"] == "La tabla de configuración no pudo revisarse completa."
            for item in result["proposal"]["unsupported"]
        ))

    def test_verified_rows_from_a_new_web_source_survive_partial_coverage(self):
        found = validate_discovery(discovery(
            section("measurements", "measurement"),
            kind="web",
            coverage_complete=False,
            unreviewed=["La fuente web solo expone una selección del mapa."],
        ))
        voltage = read_entry(
            "voltage", "Tensión", 100,
            source_id="web-2", page=None,
        )
        value = proposal(voltage, kind="web")
        value["sources"].append({
            "id": "web-2",
            "kind": "web",
            "title": "Tabla técnica abierta durante la extracción",
            "url": "https://example.test/device/registers.txt",
        })
        reason = "La tabla abierta contiene solo una parte del mapa completo."
        normalized = normalize_extraction_coverage(
            extraction(
                value,
                coverage(
                    "measurements",
                    status="incomplete",
                    read_ids=["voltage"],
                    reason=reason,
                ),
            ),
            found,
        )
        proposed = validate_proposal(normalized["proposal"])
        checked = validate_extraction_envelope(normalized, found, proposed)

        self.assertEqual(["r000001"], [
            item["id"] for item in proposed["reads"]])
        self.assertEqual("incomplete", checked["coverage"][0]["status"])
        self.assertEqual(["r000001"], checked["coverage"][0]["read_ids"])
        self.assertTrue(any(
            item["summary"] == "Revisión parcial de Sección measurements"
            and item["reason"] == reason
            for item in proposed["unsupported"]
        ))
        self.assertEqual(
            [], extraction_quality_issues(found, proposed, checked))

    def test_transport_failure_is_not_retried_automatically(self):
        request = validated_request()
        transport_error = ProviderCallError(
            "No se pudo completar la consulta al proveedor.")
        with patch("modbus_ai_provider.post_responses", side_effect=[
                transport_error,
        ]) as mocked:
            with self.assertRaises(ProviderCallError):
                request_proposal(
                    CONFIG,
                    "offline-test-key",
                    request,
                    security_mode="production",
                )
        self.assertEqual(1, mocked.call_count)

    def test_identity_source_keeps_web_search_in_discovery_and_extraction(self):
        found = discovery(
            section("measurements", "measurement"), kind="web",
        )
        voltage = read_entry("voltage", "Tensión", 100, source_id="src01")
        value = proposal(voltage, kind="web")
        value["sources"][0]["id"] = "src01"
        for proof in value["identity"]["evidence"]:
            proof["source_id"] = "src01"
        for proof in value["device"]["evidence"]:
            proof["source_id"] = "src01"
        extracted = extraction(
            value,
            coverage("sec01", read_ids=["voltage"]),
        )
        discovery_result, discovery_mock = self._request(
            [provider_response(found)], kind="identity")
        result, extraction_mock = self._request(
            [provider_response(
                extracted,
                search_query=(
                    "Example Instruments Meter 100 Modbus register list"))],
            kind="identity",
            operation="extract", found=discovery_result["discovery"],
            selected_sections=["sec01"])
        self.assertEqual("r000001", result["proposal"]["reads"][0]["id"])
        for call in [discovery_mock.call_args, extraction_mock.call_args]:
            payload = call.args[2]
            self.assertEqual(
                [{"type": "web_search", "search_context_size": "high"}],
                payload["tools"],
            )
            self.assertEqual("required", payload["tool_choice"])

    def test_refinement_keeps_the_existing_single_call_flow(self):
        voltage = read_entry("voltage", "Tensión", 100)
        previous = proposal(voltage)
        result, mocked = self._request(
            [provider_response(previous)],
            operation="refine", previous=previous,
            selected_reads=["voltage"],
        )
        self.assertEqual(1, mocked.call_count)
        self.assertEqual(
            "modulinkr_modbus_proposal",
            mocked.call_args.args[2]["text"]["format"]["name"],
        )
        self.assertEqual(
            ["voltage"],
            [item["id"] for item in result["proposal"]["reads"]],
        )

    def test_refinement_propagates_a_global_byte_order_in_one_call(self):
        first = read_entry(
            "r000001", "Tensión", 1,
            value_type="float32", count=2, byte_order=None,
        )
        second = read_entry(
            "r000002", "Corriente", 3,
            value_type="float32", count=2, byte_order=None,
        )
        previous = proposal(first, second)
        previous["pending"] = [
            {
                "scope": "read",
                "field": f"reads.{identifier}.byte_order",
                "question": "¿Qué orden de bytes utiliza?",
                "reason": "No consta.",
                "can_research_web": True,
                "web_query": "Example Meter Modbus byte order",
                "evidence": [evidence()],
            }
            for identifier in ("r000001", "r000002")
        ]
        resolved = proposal(copy.deepcopy(first))
        resolved["sources"].append({
            "id": "web01",
            "kind": "web",
            "title": "Manual técnico del fabricante",
            "url": "https://example.com/manual",
        })
        resolved["reads"][0]["byte_order"] = "CDAB"
        resolved["reads"][0]["evidence"].append({
            "source_id": "web01",
            "page": None,
            "section": "Formato de datos",
            "excerpt": "Los valores de 32 bits intercambian las palabras.",
        })

        result, mocked = self._request(
            [provider_response(
                resolved, opened_url="https://example.com/manual")],
            operation="refine", previous=previous,
            selected_reads=["r000001", "r000002"],
        )

        self.assertEqual(1, mocked.call_count)
        self.assertEqual(
            1,
            mocked.call_args.args[2]["text"]["format"]["schema"]
            ["properties"]["reads"]["maxItems"],
        )
        self.assertEqual(
            ["CDAB", "CDAB"],
            [entry["byte_order"] for entry in result["proposal"]["reads"]],
        )
        self.assertEqual([], result["proposal"]["pending"])

    def test_refinement_reverts_evidence_from_a_web_page_not_opened(self):
        previous = proposal(read_entry(
            "r000001", "Tensión", 1,
            value_type="float32", count=2, byte_order=None,
        ))
        previous["pending"] = [{
            "scope": "read",
            "field": "reads.r000001.byte_order",
            "question": "¿Qué orden de bytes utiliza?",
            "reason": "No consta.",
            "can_research_web": True,
            "web_query": "Example Meter Modbus byte order",
            "evidence": [evidence()],
        }]
        resolved = proposal(copy.deepcopy(previous["reads"][0]))
        resolved["sources"].append({
            "id": "web01",
            "kind": "web",
            "title": "Guía de integración",
            "url": "https://example.com/integration-guide",
        })
        resolved["reads"][0]["byte_order"] = "ABCD"
        resolved["reads"][0]["evidence"].append({
            "source_id": "web01",
            "page": None,
            "section": "Orden de palabras",
            "excerpt": "La palabra más significativa se transmite primero.",
        })

        result, _ = self._request(
            [provider_response(
                resolved,
                opened_url="https://example.com/other-document")],
            operation="refine", previous=previous,
            selected_reads=["r000001"],
        )

        self.assertIsNone(result["proposal"]["reads"][0]["byte_order"])
        self.assertIn(
            "reads.r000001.byte_order",
            {item["field"] for item in result["proposal"]["pending"]},
        )
        self.assertNotIn(
            "web01", {item["id"] for item in result["proposal"]["sources"]})

    def test_prompt_requires_the_exact_opened_web_url(self):
        self.assertIn(
            "source.url debe ser exactamente la URL abierta",
            SYSTEM_PROMPT,
        )
        self.assertIn("como máximo dos acciones de búsqueda", SYSTEM_PROMPT)
        self.assertIn("debe omitir el modelo exacto", SYSTEM_PROMPT)
        self.assertIn("revisa previous_proposal.sources", SYSTEM_PROMPT)
        self.assertIn(
            "scale null y offset null sin crear preguntas pendientes",
            SYSTEM_PROMPT,
        )

    def test_refinement_rejects_a_missing_selected_parameter(self):
        first = read_entry("r000001", "Tensión", 100)
        second = read_entry("r000002", "Corriente", 102)
        previous = proposal(first, second)
        incomplete = proposal(first)

        with self.assertRaisesRegex(
                ProviderCallError, "cambió la selección confirmada"):
            self._request(
                [provider_response(incomplete)],
                operation="refine",
                previous=previous,
                selected_reads=["r000001", "r000002"],
            )

    def test_prompts_make_estimates_null_and_ids_unique(self):
        discovery_payload = build_discovery_payload(
            validated_request(), CONFIG["model"])
        self.assertIn(
            "Devuelve siempre estimated_parameters como null",
            discovery_payload["input"][0]["content"],
        )
        identity_payload = build_discovery_payload(
            validated_request(kind="identity"), CONFIG["model"])
        self.assertEqual("required", identity_payload["tool_choice"])
        self.assertIn(
            "usa obligatoriamente la búsqueda web",
            identity_payload["input"][0]["content"],
        )
        self.assertIn(
            "continúa buscando otra fuente técnica",
            identity_payload["input"][0]["content"],
        )
        self.assertIn(
            "Un enlace a un manual que no abriste no es evidencia",
            identity_payload["input"][0]["content"],
        )
        self.assertIn(
            "ejecuta otra búsqueda antes de responder",
            identity_payload["input"][0]["content"],
        )
        self.assertIn(
            "los nombres de las magnitudes principales",
            identity_payload["input"][0]["content"],
        )
        self.assertIn(
            "crea secciones catalog para ese subconjunto",
            identity_payload["input"][0]["content"],
        )
        found = discovery(section("energy", "measurement", page=12))
        request = validated_request(
            operation="extract",
            found=found,
            selected_sections=["energy"],
        )
        extraction_payload = build_extraction_payload(
            request, CONFIG["model"], found)
        prompt = extraction_payload["input"][0]["content"]
        self.assertIn(
            "Nunca devuelvas complete con ambos arrays de IDs vacíos",
            prompt,
        )
        self.assertIn(
            "aplica ese dato a todas las filas compatibles",
            prompt,
        )
        self.assertIn("r000001, r000002", prompt)
        self.assertIn("IDs temporales únicos", prompt)
        self.assertIn("Nunca interpretes los dígitos hexadecimales", prompt)
        self.assertIn("count expresa cantidad de registros o bobinas", prompt)
        self.assertIn(
            "Una longitud documentada de 2 bytes equivale a count 1", prompt)
        self.assertIn(
            "Cuando una trama de solicitud documente Quantity", prompt)
        self.assertIn(
            "Distingue int16 de uint16 usando la semántica documentada", prompt)
        self.assertIn(
            "Una columna de 2 bytes solo determina count 1", prompt)
        self.assertIn(
            "valor_físico = valor_crudo * scale + offset", prompt)
        self.assertIn(
            "No uses scale 1 como valor predeterminado", prompt)
        self.assertIn(
            "Una tabla o definición técnica legible por máquina", prompt)
        self.assertIn(
            "esa advertencia limita la cobertura de lo omitido", prompt)
        self.assertIn(
            "No infieras las filas ausentes", prompt)
        self.assertIn(
            "Corrige function, address, count, type, byte_order", prompt)
        self.assertIn(
            "la dirección del registro n es n-1", prompt)
        self.assertIn(
            "investiga esa propiedad una sola vez", prompt)
        self.assertIn(
            "una fila que solo marque acceso R, W o RW", prompt)
        self.assertIn("abre su vista raw o de descarga", prompt)
        self.assertIn("category communication", prompt)
        self.assertIn(
            "abre y revisa al menos una fuente técnica mediante open_page",
            identity_payload["input"][0]["content"],
        )
        self.assertNotIn("En una recuperación", prompt)

    def test_communication_adjustments_use_the_specific_unsupported_category(self):
        found = discovery(section("settings", "communication", access="write"))
        voltage = read_entry("voltage", "Tensión", 100)
        value = proposal(voltage)
        value["unsupported"] = [{
            "category": "sequence",
            "summary": "Cambio de baudrate no se incluyó",
            "reason": "La modificación requiere reiniciar la comunicación.",
            "evidence": [evidence()],
        }]
        extracted = extraction(
            value,
            coverage("settings", read_ids=["voltage"]),
        )
        result, _ = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["settings"],
        )
        self.assertEqual(
            "communication", result["proposal"]["unsupported"][0]["category"])

    def test_calibration_is_not_mislabeled_as_communication(self):
        value = proposal(read_entry("temperature", "Temperatura", 1))
        value["unsupported"] = [{
            "category": "communication",
            "summary": "Corrección de temperatura",
            "reason": (
                "Es un ajuste de calibración del equipo y no un control "
                "operativo directo."
            ),
            "evidence": [evidence()],
        }]

        normalized = _normalize_provider_proposal(value)

        self.assertEqual("other", normalized["unsupported"][0]["category"])

    def test_normalization_drops_a_single_value_claim_built_from_many_rows(self):
        value = proposal(read_entry("voltage", "Tensión", 100))
        value["unsupported"] = [{
            "category": "data_shape",
            "summary": "Valor de varios registros no representable",
            "reason": (
                "La evidencia documenta una fila de más de dos registros."
            ),
            "evidence": [{
                "source_id": "manual-1",
                "page": 5,
                "section": "Tabla de registros",
                "excerpt": (
                    "2 Baud-Rate 0 7 R/W 3 Modbus Address 1 247 R/W "
                    "4 Parity 0 2 R/W 6 Indication mode 0 3 R/W"
                ),
            }],
        }]

        normalized = _normalize_provider_proposal(value)

        self.assertEqual([], normalized["unsupported"])

    def test_extraction_rejects_untranslated_user_facing_script(self):
        found = discovery(section("measurements", "measurement"))
        voltage = read_entry("voltage", "Tensión温度", 100)
        voltage["evidence"][0]["excerpt"] = (
            "Input register 100 contains a signed value scaled by 0.1.")
        extracted = extraction(
            proposal(voltage),
            coverage("measurements", read_ids=["voltage"]),
        )

        with self.assertRaisesRegex(
                ProviderCallError, "catálogo Modbus fiable"):
            self._request(
                [provider_response(extracted)],
                operation="extract", found=found,
                selected_sections=["measurements"],
            )

    def test_resolved_fields_remove_stale_pending_questions(self):
        found = discovery(section("measurements", "measurement"))
        voltage = read_entry("voltage", "Tensión", 100)
        value = proposal(voltage)
        value["pending"] = [
            {
                "scope": "device",
                "field": "device.name",
                "question": "¿Qué nombre debe mostrarse?",
                "reason": "El modelo dejó una pregunta redundante.",
                "can_research_web": False,
                "web_query": None,
                "evidence": [evidence()],
            },
            {
                "scope": "read",
                "field": "reads.voltage.address",
                "question": "¿Cuál es la dirección?",
                "reason": "El modelo dejó una pregunta redundante.",
                "can_research_web": False,
                "web_query": None,
                "evidence": [evidence()],
            },
        ]
        extracted = extraction(
            value,
            coverage("measurements", read_ids=["voltage"]),
        )

        result, _ = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["measurements"],
        )

        self.assertEqual([], result["proposal"]["pending"])
        self.assertTrue(result["ready"])

    def test_16_bit_value_drops_a_stale_byte_order_question(self):
        found = discovery(section("measurements", "measurement"))
        temperature = read_entry(
            "temperature", "Temperatura", 1,
            value_type="int16", count=1, byte_order=None,
        )
        value = proposal(temperature)
        value["pending"] = [{
            "scope": "read",
            "field": "reads.temperature.byte_order",
            "question": "¿Qué orden de bytes utiliza?",
            "reason": "Pregunta redundante para un tipo de 16 bits.",
            "can_research_web": False,
            "web_query": None,
            "evidence": [evidence()],
        }]
        extracted = extraction(
            value,
            coverage("measurements", read_ids=["temperature"]),
        )

        result, _ = self._request(
            [provider_response(extracted)],
            operation="extract",
            found=found,
            selected_sections=["measurements"],
        )

        self.assertEqual([], result["proposal"]["pending"])
        self.assertTrue(result["ready"])

    def test_pending_research_uses_parameter_name_instead_of_temporary_id(self):
        value = proposal(read_entry("r000001", "Temperatura", 1))
        value["pending"] = [{
            "scope": "read",
            "field": "reads.r000001.scale",
            "question": "¿Cuál es la escala?",
            "reason": "No consta.",
            "can_research_web": True,
            "web_query": "Example Meter Modbus reads r000001 scale",
            "evidence": [evidence()],
        }]

        _prepare_pending_research(value)

        query = value["pending"][0]["web_query"]
        self.assertIn('"Temperatura"', query)
        self.assertIn("scale", query)
        self.assertNotIn("r000001", query)

    def test_type_research_uses_signedness_and_range_terms(self):
        value = proposal(read_entry("r000001", "Temperatura", 1))
        value["reads"][0]["type"] = None
        value["pending"] = [{
            "scope": "read",
            "field": "reads.r000001.type",
            "question": "¿Cuál es el tipo?",
            "reason": "No consta.",
            "can_research_web": True,
            "web_query": "Example Meter Modbus temperature type",
            "evidence": [evidence()],
        }]

        _prepare_pending_research(value)

        query = value["pending"][0]["web_query"]
        self.assertIn('"Temperatura"', query)
        self.assertIn("signed unsigned", query)
        self.assertIn("negative range", query)

    def test_refine_automatically_refreshes_all_researchable_queries(self):
        value = proposal(read_entry("r000001", "Temperatura", 1))
        value["pending"] = [{
            "scope": "read",
            "field": "reads.r000001.scale",
            "question": "¿Cuál es la escala?",
            "reason": "No consta.",
            "can_research_web": True,
            "web_query": "Example Meter Modbus reads r000001 scale",
            "evidence": [evidence()],
        }]

        validated = validate_assistant_request(request_body(
            operation="refine", previous=value,
            selected_reads=["r000001"],
        ))

        self.assertEqual(1, len(validated["web_queries"]))
        self.assertIn('"Temperatura"', validated["web_queries"][0])
        self.assertNotIn("r000001", validated["web_queries"][0])
        self.assertTrue(validated["use_web"])

    def test_refine_researches_the_documented_factory_unit(self):
        value = proposal(read_entry(
            "r000001", "Temperatura", 7,
            value_type="int16", count=1, byte_order=None,
        ))
        value["reads"][0]["unit"] = None
        value["pending"] = [{
            "scope": "read",
            "field": "reads.r000001.unit",
            "question": "¿Cuál es la unidad efectiva?",
            "reason": "El dispositivo permite varias unidades.",
            "can_research_web": False,
            "web_query": None,
            "evidence": [evidence()],
        }]

        validated = validate_assistant_request(request_body(
            operation="refine", previous=value,
            selected_reads=["r000001"],
        ))

        self.assertEqual(1, len(validated["web_queries"]))
        self.assertIn('"Temperatura"', validated["web_queries"][0])
        self.assertIn('"factory configured"', validated["web_queries"][0])
        self.assertIn("default unit", validated["web_queries"][0])
        self.assertTrue(validated["use_web"])
        self.assertIn(
            "debes usar ese valor de fábrica",
            SYSTEM_PROMPT,
        )

    def test_refine_consolidates_global_byte_order_research(self):
        first = read_entry(
            "r000001", "Tensión", 1,
            value_type="float32", count=2, byte_order=None,
        )
        second = read_entry(
            "r000002", "Corriente", 3,
            value_type="float32", count=2, byte_order=None,
        )
        value = proposal(first, second)
        value["pending"] = [
            {
                "scope": "read",
                "field": f"reads.{identifier}.byte_order",
                "question": "¿Qué orden de bytes utiliza?",
                "reason": "No consta.",
                "can_research_web": True,
                "web_query": f"Example Meter Modbus {name} byte order",
                "evidence": [evidence()],
            }
            for identifier, name in (
                ("r000001", "Tensión"), ("r000002", "Corriente"),
            )
        ]

        validated = validate_assistant_request(request_body(
            operation="refine", previous=value,
            selected_reads=["r000001", "r000002"],
        ))

        self.assertEqual(1, len(validated["web_queries"]))
        self.assertIn("Modbus", validated["web_queries"][0])
        self.assertIn('"most significant register"', validated["web_queries"][0])
        self.assertIn('"big endian"', validated["web_queries"][0])
        self.assertIn("32-bit registers", validated["web_queries"][0])
        self.assertNotIn("manual técnico", validated["web_queries"][0])
        self.assertNotIn("Tensión", validated["web_queries"][0])
        self.assertIn(
            "Ejecuta primero cada cadena recibida de forma literal",
            SYSTEM_PROMPT,
        )
        self.assertIn(
            "intercambio de las dos palabras de 16 bits CDAB",
            SYSTEM_PROMPT,
        )
        self.assertIn(
            "consultas alternativas separadas",
            SYSTEM_PROMPT,
        )
        self.assertIn(
            "palabra más significativa primero equivalen a ABCD",
            SYSTEM_PROMPT,
        )
        self.assertIn(
            "abre otro resultado oficial",
            SYSTEM_PROMPT,
        )
        self.assertIn(
            "no vuelvas a abrirlas como única investigación",
            SYSTEM_PROMPT,
        )

    def test_global_byte_order_refinement_uses_one_representative_entry(self):
        first = read_entry(
            "r000001", "Tensión", 1,
            value_type="float32", count=2, byte_order=None,
        )
        second = read_entry(
            "r000002", "Corriente", 3,
            value_type="float32", count=2, byte_order=None,
        )
        value = proposal(first, second)
        value["pending"] = [
            {
                "scope": "read",
                "field": f"reads.{identifier}.byte_order",
                "question": "¿Qué orden de bytes utiliza?",
                "reason": "No consta.",
                "can_research_web": True,
                "web_query": "Example Meter Modbus byte order",
                "evidence": [evidence()],
            }
            for identifier in ("r000001", "r000002")
        ]
        request = validate_assistant_request(request_body(
            operation="refine", previous=value,
            selected_reads=["r000001", "r000002"],
        ))

        compact, fields = _compact_global_byte_order_refinement(request)

        self.assertEqual(["r000001"], compact["selected"]["reads"])
        self.assertEqual([], compact["selected"]["writes"])
        self.assertEqual([
            "reads.r000001.byte_order", "reads.r000002.byte_order",
        ], fields)
        self.assertEqual(2, len(compact["previous_proposal"]["reads"]))

    def test_global_byte_order_propagates_only_new_web_evidence(self):
        first = read_entry(
            "r000001", "Tensión", 1,
            value_type="float32", count=2, byte_order=None,
        )
        second = read_entry(
            "r000002", "Corriente", 3,
            value_type="float32", count=2, byte_order=None,
        )
        previous = proposal(first, second)
        fields = [
            "reads.r000001.byte_order", "reads.r000002.byte_order",
        ]
        previous["pending"] = [
            {
                "scope": "read",
                "field": field,
                "question": "¿Qué orden de bytes utiliza?",
                "reason": "No consta.",
                "can_research_web": True,
                "web_query": "Example Meter Modbus byte order",
                "evidence": [evidence()],
            }
            for field in fields
        ]
        refined = copy.deepcopy(previous)
        refined["sources"].append({
            "id": "web01",
            "kind": "web",
            "title": "Manual técnico del fabricante",
            "url": "https://example.com/manual",
        })
        refined["reads"][0]["byte_order"] = "CDAB"
        refined["reads"][0]["evidence"].append({
            "source_id": "web01",
            "page": None,
            "section": "Formato de datos",
            "excerpt": "Los valores de 32 bits intercambian las palabras.",
        })

        propagated = _propagate_global_byte_order(
            refined, previous, fields)

        self.assertEqual(
            ["CDAB", "CDAB"],
            [entry["byte_order"] for entry in propagated["reads"]],
        )
        self.assertEqual([], propagated["pending"])
        self.assertIn(
            "web01",
            {item["source_id"]
             for item in propagated["reads"][1]["evidence"]},
        )

        without_new_source = _propagate_global_byte_order(
            previous, previous, fields)
        self.assertEqual(
            [None, None],
            [entry["byte_order"] for entry in without_new_source["reads"]],
        )
        self.assertEqual(2, len(without_new_source["pending"]))

    def test_global_function_address_and_order_use_one_representative(self):
        first = read_entry(
            "r000001", "Corriente A", None,
            value_type="float32", count=2, byte_order=None,
        )
        first["function"] = None
        first["evidence"][0]["excerpt"] = (
            "Current A ... Register 3000 ... A ... 2 ... FLOAT32 ... R")
        second = read_entry(
            "r000002", "Tensión A-B", None,
            value_type="float32", count=2, byte_order=None,
        )
        second["function"] = None
        second["evidence"][0]["excerpt"] = (
            "Voltage A-B ... Register 3020 ... V ... 2 ... FLOAT32 ... R")
        previous = proposal(first, second)
        previous["pending"] = [
            {
                "scope": "read",
                "field": f"reads.{identifier}.{attribute}",
                "question": "¿Qué convención global utiliza?",
                "reason": "No consta.",
                "can_research_web": True,
                "web_query": f"Example Meter Modbus {attribute}",
                "evidence": [evidence()],
            }
            for identifier in ("r000001", "r000002")
            for attribute in ("function", "address", "byte_order")
        ]
        request = validate_assistant_request(request_body(
            operation="refine", previous=previous,
            selected_reads=["r000001", "r000002"],
        ))

        compact, plan = _compact_global_refinement(request)

        self.assertEqual(["r000001"], compact["selected"]["reads"])
        self.assertEqual(
            {"function", "address", "byte_order"}, set(plan))
        self.assertTrue(all(len(fields) == 2 for fields in plan.values()))

        refined = copy.deepcopy(previous)
        refined["sources"].append({
            "id": "web01",
            "kind": "web",
            "title": "Convenciones Modbus oficiales",
            "url": "https://example.com/modbus-conventions",
        })
        refined["reads"][0].update({
            "function": "read_holding_registers",
            "address": 2999,
            "byte_order": "ABCD",
        })
        refined["reads"][0]["evidence"].append({
            "source_id": "web01",
            "page": None,
            "section": "Convenciones globales",
            "excerpt": (
                "FC03; register 3000 uses address 2999; most significant "
                "register and byte first."),
        })

        propagated = _propagate_global_refinement(refined, previous, plan)

        self.assertEqual(
            ["read_holding_registers", "read_holding_registers"],
            [entry["function"] for entry in propagated["reads"]],
        )
        self.assertEqual(
            [2999, 3019],
            [entry["address"] for entry in propagated["reads"]],
        )
        self.assertEqual(
            ["ABCD", "ABCD"],
            [entry["byte_order"] for entry in propagated["reads"]],
        )
        self.assertEqual([], propagated["pending"])

    def test_global_plan_covers_selected_entries_beyond_pending_limit(self):
        entries = []
        for number in range(1, 33):
            identifier = f"r{number:06d}"
            entry = read_entry(
                identifier, f"Medida {number}", None,
                value_type="float32", count=2, byte_order=None,
            )
            entry["function"] = None
            register = 2700 if number == 12 else 3000 + number
            entry["evidence"][0]["excerpt"] = (
                f"Measure {number}; Register {register}; FLOAT32; R")
            entries.append(entry)
        previous = proposal(*entries)
        previous["pending"] = [
            {
                "scope": "read",
                "field": f"reads.{entry['id']}.{attribute}",
                "question": "¿Qué convención global utiliza?",
                "reason": "No consta.",
                "can_research_web": True,
                "web_query": f"Example Meter Modbus {attribute}",
                "evidence": [evidence()],
            }
            for attribute in ("function", "address", "byte_order")
            for entry in entries
        ][:64]
        request = validate_assistant_request(request_body(
            operation="refine", previous=previous,
            selected_reads=[entry["id"] for entry in entries],
        ))

        compact, plan = _compact_global_refinement(request)

        self.assertEqual(["r000012"], compact["selected"]["reads"])
        self.assertEqual(
            {"function", "address"}, set(plan))
        self.assertEqual(32, len(plan["function"]))
        self.assertEqual(32, len(plan["address"]))
        self.assertEqual("reads.r000012.function", plan["function"][0])
        self.assertEqual("reads.r000012.address", plan["address"][0])

    def test_global_address_skips_only_entries_with_ambiguous_coordinates(self):
        first = read_entry(
            "r000001", "Corriente A", None,
            value_type="float32", count=2, byte_order=None,
        )
        first["evidence"][0]["excerpt"] = (
            "Current A; Register 3000; FLOAT32; R")
        ambiguous = read_entry(
            "r000002", "Estado digital", None,
            value_type="float32", count=2, byte_order=None,
        )
        ambiguous["evidence"][0]["excerpt"] = (
            "Digital state | 2400 | bit 0 | absolute 38400")
        previous = proposal(first, ambiguous)
        previous["pending"] = [
            {
                "scope": "read",
                "field": f"reads.{identifier}.address",
                "question": "¿Qué convención global utiliza?",
                "reason": "No consta.",
                "can_research_web": True,
                "web_query": "Example Meter Modbus address",
                "evidence": [evidence()],
            }
            for identifier in ("r000001", "r000002")
        ]
        refined = copy.deepcopy(previous)
        refined["sources"].append({
            "id": "web01",
            "kind": "web",
            "title": "Convención de dirección oficial",
            "url": "https://example.com/addressing",
        })
        refined["reads"][0]["address"] = 2999
        refined["reads"][0]["evidence"].append({
            "source_id": "web01",
            "page": None,
            "section": "Direcciones",
            "excerpt": "Register 3000 uses protocol address 2999.",
        })

        propagated = _propagate_global_refinement(
            refined, previous,
            {"address": [
                "reads.r000001.address", "reads.r000002.address"]},
        )

        self.assertEqual(2999, propagated["reads"][0]["address"])
        self.assertIsNone(propagated["reads"][1]["address"])
        self.assertEqual(
            ["reads.r000002.address"],
            [item["field"] for item in propagated["pending"]],
        )

    def test_refinement_normalization_keeps_incomplete_entries(self):
        complete = read_entry(
            "r000001", "Corriente A", 2999,
            value_type="float32", count=2, byte_order="ABCD",
        )
        incomplete = read_entry(
            "r000002", "Tensión A-B", None,
            value_type="float32", count=2, byte_order=None,
        )
        incomplete["function"] = None
        value = proposal(complete, incomplete)

        normalized = _normalize_provider_proposal(
            value, {"operation": "refine", "current": {}})

        self.assertEqual(
            ["r000001", "r000002"],
            [entry["id"] for entry in normalized["reads"]],
        )

    def test_remote_attachment_keeps_its_original_web_provenance(self):
        value = proposal(read_entry("r000001", "Corriente A", 3000))
        value["sources"] = [{
            "id": "srcu1",
            "kind": "user",
            "title": "register-list.xlsx",
            "url": None,
        }]
        found = discovery(section("sec01", "measurement"))
        found["sources"] = [{
            "id": "src03",
            "kind": "web",
            "title": "register-list.xlsx",
            "url": "https://example.com/register-list.xlsx",
        }]

        _restore_discovery_source_metadata(value, found)

        self.assertEqual("web", value["sources"][0]["kind"])
        self.assertEqual(
            "https://example.com/register-list.xlsx",
            value["sources"][0]["url"],
        )

    def test_discovery_evidence_reuses_retransmitted_file_source(self):
        value = proposal(read_entry("r000001", "Corriente A", 3000))
        value["sources"] = [{
            "id": "src_file",
            "kind": "web",
            "title": "register-list.xlsx",
            "url": "https://example.com/register-list.xlsx",
        }]
        value["unsupported"] = [{
            "category": "catalog_limit",
            "summary": "Límite del catálogo",
            "reason": "Se omitieron parámetros documentados por capacidad.",
            "evidence": [evidence("src03")],
        }]
        found = discovery(section("sec01", "measurement"))
        found["sources"] = [{
            "id": "src03",
            "kind": "web",
            "title": "register-list.xlsx",
            "url": "https://example.com/register-list.xlsx",
        }]

        _reconcile_discovery_evidence_sources(value, found)

        self.assertEqual(
            "src_file",
            value["unsupported"][0]["evidence"][0]["source_id"],
        )
        self.assertEqual(
            ["src_file"], [item["id"] for item in value["sources"]])

    def test_current_bus_and_device_evidence_declares_user_source(self):
        value = proposal(read_entry("r000001", "Corriente A", 3000))
        value["bus"]["evidence"] = [evidence("user")]
        value["device"]["evidence"] = [evidence("user")]

        _declare_current_evidence_source(value, {"current": {"bus": {}}})

        self.assertEqual("user", value["sources"][-1]["id"])
        self.assertEqual("user", value["sources"][-1]["kind"])
        self.assertEqual(
            "Configuración actual del formulario",
            value["sources"][-1]["title"],
        )

    def test_current_source_is_not_declared_for_technical_entries(self):
        value = proposal(read_entry("r000001", "Corriente A", 3000))
        value["bus"]["evidence"] = [evidence("user")]
        value["reads"][0]["evidence"] = [evidence("user")]

        _declare_current_evidence_source(value, {"current": {"bus": {}}})

        self.assertNotIn("user", {item["id"] for item in value["sources"]})

    def test_url_shaped_source_id_is_canonicalized_with_its_evidence(self):
        value = proposal(read_entry("r000001", "Corriente A", 3000))
        invalid = "https://example.com/technical-source"
        value["sources"].append({
            "id": invalid,
            "kind": "web",
            "title": "Fuente técnica",
            "url": invalid,
        })
        value["reads"][0]["evidence"].append(evidence(invalid))
        value["pending"] = [{
            "scope": "read",
            "field": "reads.r000001.byte_order",
            "question": "¿Qué orden utiliza?",
            "reason": "La fuente debe confirmarlo.",
            "can_research_web": True,
            "web_query": "Example byte order",
            "evidence": [evidence(invalid)],
        }]

        _canonicalize_source_ids(value)

        canonical = value["sources"][-1]["id"]
        self.assertRegex(canonical, r"^source\d{2}$")
        self.assertEqual(
            canonical, value["reads"][0]["evidence"][-1]["source_id"])
        self.assertEqual(
            canonical, value["pending"][0]["evidence"][0]["source_id"])

    def test_refinement_discards_unused_web_sources_not_opened(self):
        previous = proposal(read_entry("r000001", "Corriente A", 2999))
        raw = copy.deepcopy(previous)
        raw["sources"].extend([
            {
                "id": "opened",
                "kind": "web",
                "title": "Fuente abierta",
                "url": "https://example.com/opened",
            },
            {
                "id": "unopened",
                "kind": "web",
                "title": "Fuente no abierta",
                "url": "https://example.com/unopened",
            },
        ])
        data = {
            "output": [{
                "type": "web_search_call",
                "action": {
                    "type": "open_page",
                    "url": "https://example.com/opened",
                },
            }],
        }
        request = {"operation": "refine", "previous_proposal": previous}

        _drop_unused_unopened_web_sources(data, raw, request)

        self.assertEqual(
            {"manual-1", "opened"},
            {item["id"] for item in raw["sources"]},
        )

    def test_web_rows_without_coordinate_semantics_become_pending(self):
        current = read_entry(
            "r000001", "Current A", 3000,
            value_type="float32", count=2, byte_order=None,
        )
        current["evidence"][0]["excerpt"] = (
            "Current A 3000 A 2 FLOAT32 R")
        value = proposal(current, kind="web")

        normalized = _normalize_provider_proposal(value)

        self.assertIsNone(normalized["reads"][0]["function"])
        self.assertIsNone(normalized["reads"][0]["address"])
        pending = {item["field"] for item in normalized["pending"]}
        self.assertIn("reads.r000001.function", pending)
        self.assertIn("reads.r000001.address", pending)

    def test_ambiguous_unit_becomes_researchable_pending(self):
        temperature = read_entry(
            "r000001", "Temperatura", 1,
            value_type="int16", count=1, byte_order=None,
        )
        temperature["unit"] = "°C o °F"
        value = proposal(temperature)

        normalized = _normalize_provider_proposal(value)
        validated = validate_proposal(normalized)

        self.assertIsNone(validated["reads"][0]["unit"])
        item = next(
            pending for pending in validated["pending"]
            if pending["field"] == "reads.r000001.unit"
        )
        self.assertTrue(item["can_research_web"])
        self.assertIn("Temperatura", item["web_query"])
        self.assertIn("unidad efectiva", item["question"])

    def test_web_rows_keep_explicit_function_and_pdu_address(self):
        current = read_entry(
            "r000001", "Current A", 16,
            value_type="float32", count=2, byte_order="ABCD",
        )
        current["evidence"][0].update({
            "section": "Input registers",
            "excerpt": "Register address 0x0010, Current A, FLOAT32",
        })
        value = proposal(current, kind="web")

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(
            "read_input_registers", normalized["reads"][0]["function"])
        self.assertEqual(16, normalized["reads"][0]["address"])

    def test_spanish_function_and_request_frame_evidence_keep_coordinates(self):
        temperature = read_entry(
            "r000001", "Temperatura", 1,
            value_type="int16", count=1, byte_order=None,
        )
        temperature["evidence"][0].update({
            "section": "Trama de lectura",
            "excerpt": (
                "Lectura por función 4. Baca Suhu "
                "01 04 00 01 00 01 AA BB; registro 0001."
            ),
        })
        value = proposal(temperature, kind="web")

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(
            "read_input_registers", normalized["reads"][0]["function"])
        self.assertEqual(1, normalized["reads"][0]["address"])

    def test_direct_manual_discovered_for_identity_is_retained_for_refinement(self):
        value = proposal(read_entry("r000001", "Temperatura", 1), kind="web")
        value["sources"][0].update({
            "title": "Página técnica",
            "url": "https://example.test/device",
        })
        found = discovery(section("measurements", "measurement"), kind="web")
        found["sources"] = [
            {
                "id": "s1", "kind": "web", "title": "Página técnica",
                "url": "https://example.test/device",
            },
            {
                "id": "s2", "kind": "web", "title": "Manual técnico PDF",
                "url": "https://example.test/device-manual.pdf",
            },
        ]

        _retain_discovery_reference_sources(value, found)

        self.assertEqual(2, len(value["sources"]))
        self.assertEqual(
            "https://example.test/device-manual.pdf",
            value["sources"][1]["url"],
        )

    def test_register_number_uses_opened_n_minus_one_convention(self):
        current = read_entry(
            "r000001", "Current A", None,
            value_type="float32", count=2, byte_order="ABCD",
        )
        current["evidence"] = [
            {
                "source_id": "manual-1",
                "page": None,
                "section": "Register List",
                "excerpt": "Current A | 3000 | FLOAT32 | R | A",
            },
            {
                "source_id": "manual-1",
                "page": None,
                "section": "Addressing convention",
                "excerpt": (
                    "The starting address represents the (n-1)th register "
                    "from the beginning of this range."
                ),
            },
        ]
        value = proposal(current, kind="web")

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(2999, normalized["reads"][0]["address"])

    def test_reference_and_hex_offset_canonicalize_pdu_address(self):
        current = read_entry(
            "r000001", "Demanda de corriente", 601,
            value_type="float32", count=2, byte_order="ABCD",
        )
        current["evidence"][0]["excerpt"] = (
            "30259 current demand. 4 Float Amps 01 02")
        value = proposal(current)

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(258, normalized["reads"][0]["address"])

    def test_reference_without_matching_hex_offset_keeps_address(self):
        current = read_entry(
            "r000001", "Demanda de corriente", 601,
            value_type="float32", count=2, byte_order="ABCD",
        )
        current["evidence"][0]["excerpt"] = (
            "30259 current demand. 4 Float Amps 01 03")
        value = proposal(current)

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(601, normalized["reads"][0]["address"])

    def test_web_rows_keep_hex_coordinate_in_a_register_table(self):
        temperature = read_entry(
            "r000001", "Temperatura", 1,
            value_type="int16", count=1, byte_order=None,
        )
        temperature["function"] = "read_holding_registers"
        temperature["evidence"][0].update({
            "section": "Register configuration",
            "excerpt": "0x04 (Read input register) 0x0001 (2 Bytes) Temperature",
        })
        value = proposal(temperature, kind="web")

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(
            "read_input_registers", normalized["reads"][0]["function"])
        self.assertEqual(1, normalized["reads"][0]["address"])

    def test_prefixed_modbus_frame_proves_function_and_address(self):
        temperature = read_entry(
            "r000001", "Temperatura", 1,
            value_type="int16", count=1, byte_order=None,
        )
        temperature["function"] = None
        temperature["evidence"][0].update({
            "section": "MODBUS COMMAND",
            "excerpt": "0x01 0x04 0x00 0x01 0x00 0x01",
        })
        value = proposal(temperature, kind="web")

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(
            "read_input_registers", normalized["reads"][0]["function"])
        self.assertEqual(1, normalized["reads"][0]["address"])

    def test_web_write_keeps_function_call_and_matching_hex_coordinate(self):
        correction = {
            "id": "w000001",
            "name": "Consigna de temperatura",
            "function": "write_multiple_registers",
            "address": 259,
            "count": 1,
            "type": "int16",
            "byte_order": None,
            "scale": 0.1,
            "offset": 0,
            "unit": "°C",
            "evidence": [evidence()],
            "purpose": "operational",
        }
        correction["evidence"][0].update({
            "section": "Código de ejemplo de escritura",
            "excerpt": (
                "Temperature setpoint: write_single_reg(ser, DEV_ADDR, "
                "0x06, 0x0103, int(TC*10))"
            ),
        })
        value = proposal(kind="web")
        value["writes"] = [correction]

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(
            "write_single_register", normalized["writes"][0]["function"])
        self.assertEqual(259, normalized["writes"][0]["address"])

    def test_calibration_write_is_excluded_as_non_operational(self):
        correction = {
            "id": "w000001",
            "name": "Corrección de temperatura",
            "function": "write_single_register",
            "address": 259,
            "count": 1,
            "type": "int16",
            "byte_order": None,
            "scale": 0.1,
            "offset": 0,
            "unit": "°C",
            "evidence": [evidence()],
            "purpose": "operational",
        }
        correction["evidence"][0]["excerpt"] = (
            "Temperature correction(/10) -10.0~10.0")
        value = proposal(kind="web")
        value["writes"] = [correction]

        normalized = _normalize_provider_proposal(value)

        self.assertEqual([], normalized["writes"])
        self.assertTrue(any(
            item["category"] == "other"
            and "Corrección de temperatura" in item["summary"]
            for item in normalized["unsupported"]
        ))

    def test_unselected_mixed_settings_remain_visible_as_exclusions(self):
        value = proposal(read_entry("r000001", "Temperatura", 1), kind="web")
        settings = section(
            "settings", "communication", access="mixed",
            applicability="information", source_id="manual-1",
        )
        settings["title"] = "Dirección, velocidad y correcciones"
        settings["evidence"] = [evidence("manual-1")]
        settings["evidence"][0]["excerpt"] = (
            "Device Address, Baud Rate, Temperature Correction and "
            "Humidity Correction"
        )

        _append_detected_non_operational_sections(
            value, {"sections": [settings]}, ["measurements"])

        categories = {item["category"] for item in value["unsupported"]}
        self.assertEqual({"communication", "other"}, categories)
        self.assertEqual([], value["writes"])
        self.assertTrue(all(
            "no se añaden como acciones" in item["reason"]
            for item in value["unsupported"]
        ))

    def test_persistent_output_mode_is_not_an_operational_write(self):
        mode = {
            "id": "w000001",
            "name": "Modo de control de salida 1",
            "function": "write_single_register",
            "address": 4096,
            "count": 1,
            "type": "uint16",
            "byte_order": None,
            "scale": None,
            "offset": None,
            "unit": None,
            "evidence": [evidence()],
            "purpose": "operational",
        }
        value = proposal(kind="web")
        value["writes"] = [mode]

        normalized = _normalize_provider_proposal(value)

        self.assertEqual([], normalized["writes"])
        self.assertEqual("other", normalized["unsupported"][-1]["category"])

    def test_function_code_hex_does_not_prove_pdu_address(self):
        current = read_entry(
            "r000001", "Current A", 3000,
            value_type="float32", count=2, byte_order="ABCD",
        )
        current["evidence"][0]["excerpt"] = (
            "Function 0x04, Current A, Register 3000, FLOAT32")
        value = proposal(current, kind="web")

        normalized = _normalize_provider_proposal(value)

        self.assertEqual(
            "read_input_registers", normalized["reads"][0]["function"])
        self.assertIsNone(normalized["reads"][0]["address"])
        self.assertIn(
            "reads.r000001.address",
            {item["field"] for item in normalized["pending"]},
        )

    def test_refinement_accepts_evidenced_technical_corrections(self):
        old_entry = read_entry("r000001", "Temperatura", 1)
        old_entry["scale"] = None
        old_entry["unit"] = None
        previous = proposal(old_entry)
        previous["pending"] = [{
            "scope": "read",
            "field": "reads.r000001.scale",
            "question": "¿Cuál es la escala?",
            "reason": "No consta.",
            "can_research_web": True,
            "web_query": "consulta",
            "evidence": [evidence()],
        }]
        changed = proposal(read_entry("r000001", "Nombre alterado", 999))
        changed["reads"][0]["scale"] = 0.1
        changed["reads"][0]["unit"] = "°C"
        changed["sources"].append({
            "id": "web01", "kind": "web", "title": "Ficha técnica",
            "url": "https://example.test/registers",
        })
        changed["reads"][0]["evidence"].append({
            "source_id": "web01", "page": None,
            "section": "Mapa corregido",
            "excerpt": "La dirección PDU es 999 y la escala es 0.1.",
        })

        merged = _merge_refinement_with_previous(changed, previous)

        self.assertEqual("Temperatura", merged["reads"][0]["name"])
        self.assertEqual(999, merged["reads"][0]["address"])
        self.assertEqual(0.1, merged["reads"][0]["scale"])
        self.assertEqual("°C", merged["reads"][0]["unit"])

    def test_refinement_rejects_a_technical_change_without_new_evidence(self):
        previous = proposal(read_entry("r000001", "Temperatura", 1))
        changed = copy.deepcopy(previous)
        changed["reads"][0]["address"] = 999

        merged = _merge_refinement_with_previous(changed, previous)

        self.assertEqual(1, merged["reads"][0]["address"])

    def test_refinement_preserves_previous_evidence_when_new_is_empty(self):
        previous = proposal(read_entry("r000001", "Temperatura", 1))
        changed = copy.deepcopy(previous)
        changed["reads"][0]["evidence"] = []

        merged = _merge_refinement_with_previous(changed, previous)

        self.assertEqual(
            previous["reads"][0]["evidence"],
            merged["reads"][0]["evidence"],
        )

    def test_refinement_renumbers_colliding_source_ids_and_evidence(self):
        previous = proposal(
            read_entry("r000001", "Temperatura", 1),
            read_entry("r000002", "Humedad", 3),
        )
        previous["sources"][0].update({
            "id": "src01", "title": "Fuente anterior",
            "url": "https://example.test/old",
        })
        for entry in previous["reads"]:
            entry["evidence"][0]["source_id"] = "src01"
        changed = proposal(copy.deepcopy(previous["reads"][0]))
        changed["sources"] = [{
            "id": "src01", "kind": "web", "title": "Fuente nueva",
            "url": "https://example.test/new",
        }]
        changed["reads"][0]["evidence"][0]["source_id"] = "src01"

        merged = _merge_refinement_with_previous(changed, previous)

        source_ids = [item["id"] for item in merged["sources"]]
        self.assertEqual(len(source_ids), len(set(source_ids)))
        self.assertIn("src01", source_ids)
        evidence_sources = {
            item["source_id"] for item in merged["reads"][0]["evidence"]
        }
        self.assertIn("src01", evidence_sources)
        new_evidence_sources = evidence_sources - {"src01"}
        self.assertEqual(1, len(new_evidence_sources))
        self.assertTrue(new_evidence_sources.issubset(source_ids))
        self.assertEqual(
            "src01", merged["reads"][1]["evidence"][0]["source_id"])


if __name__ == "__main__":
    unittest.main()
