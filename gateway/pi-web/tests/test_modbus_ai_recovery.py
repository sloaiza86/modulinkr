"""Pruebas offline del flujo escalonado del asistente Modbus."""

from __future__ import annotations

import base64
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
    discovery_quality_issues,
    extraction_envelope_schema,
    extraction_quality_issues,
    validate_discovery,
    validate_extraction_envelope,
)
from modbus_ai_contract import CONTRACT_VERSION, validate_proposal  # noqa: E402
from modbus_ai_provider import (  # noqa: E402
    ProviderCallError,
    build_discovery_payload,
    build_extraction_payload,
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


def section(section_id, category, *, access="read", applicability="catalog",
            estimated_parameters=1, source_id="manual-1", page=12):
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
        "evidence": [evidence(source_id, page)],
    }


def discovery(*sections, kind="manual", coverage_complete=True,
              unreviewed=None):
    return {
        "discovery_version": DISCOVERY_VERSION,
        "sources": [source(kind)],
        "identity": identity(),
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


def provider_response(value):
    return {
        "status": "completed",
        "output": [{
            "type": "message",
            "content": [{
                "type": "output_text",
                "text": json.dumps(value, ensure_ascii=False),
            }],
        }],
    }


def request_body(*, kind="manual", previous=None, selected_reads=None):
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
        "source": request_source,
        "confirmed_identity": None,
        "current": {
            "bus": {"baudrate": 9600, "parity": "N", "stopbits": 1},
            "device": {"name": "", "default_slave_id": 1,
                       "desired_slave_id": 1},
        },
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

    def test_manual_is_attached_to_both_initial_stages(self):
        request = validated_request()
        found = discovery(
            section("measurements", "measurement"),
        )
        for payload in (
                build_discovery_payload(request, "gpt-5.6"),
                build_extraction_payload(request, "gpt-5.6", found)):
            content = payload["input"][1]["content"]
            self.assertEqual("input_file", content[1]["type"])
            self.assertTrue(content[1]["file_data"].startswith(
                "data:application/pdf;base64,"))
            self.assertNotIn("pdf_base64", content[0]["text"])

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
    def _request(self, responses, *, kind="manual", previous=None,
                 selected_reads=None):
        request = validated_request(
            kind=kind, previous=previous, selected_reads=selected_reads)
        with patch("modbus_ai_provider.post_responses",
                   side_effect=responses) as mocked:
            result = request_proposal(
                CONFIG,
                "offline-test-key",
                request,
                security_mode="production",
            )
        return result, mocked

    def test_normal_initial_flow_uses_discovery_and_extraction_only(self):
        found = discovery(
            section("measurements", "measurement", estimated_parameters=2),
        )
        voltage = read_entry("voltage", "Tensión", 100)
        current = read_entry("current", "Corriente", 102)
        extracted = extraction(
            proposal(voltage, current),
            coverage("measurements", read_ids=["voltage", "current"]),
        )
        result, mocked = self._request([
            provider_response(found), provider_response(extracted),
        ])
        self.assertEqual(2, mocked.call_count)
        self.assertEqual(
            ["modulinkr_modbus_discovery", "modulinkr_modbus_catalog"],
            [call.args[2]["text"]["format"]["name"]
             for call in mocked.call_args_list],
        )
        self.assertEqual(
            ["voltage", "current"],
            [item["id"] for item in result["proposal"]["reads"]],
        )

    def test_explained_incompatible_entry_keeps_valid_catalog_without_recovery(self):
        found = discovery(
            section("measurements", "measurement", estimated_parameters=20),
        )
        voltage = read_entry("voltage", "Tensión", 100)
        energy64 = read_entry(
            "energy64", "Energía acumulada de 64 bits", 200,
            value_type="uint32", count=4,
        )
        extracted = extraction(
            proposal(voltage, energy64),
            coverage(
                "measurements",
                read_ids=["voltage", "energy64"],
            ),
        )

        result, mocked = self._request([
            provider_response(found), provider_response(extracted),
        ])

        self.assertEqual(2, mocked.call_count)
        self.assertEqual(
            ["voltage"],
            [item["id"] for item in result["proposal"]["reads"]],
        )
        self.assertEqual(
            ["data_shape"],
            [item["category"] for item in result["proposal"]["unsupported"]],
        )

    def test_empty_extraction_recovers_once_and_adds_candidates(self):
        found = discovery(
            section("measurements", "measurement", estimated_parameters=1),
        )
        empty = extraction(
            proposal(),
            coverage("measurements"),
            summary="No se extrajeron parámetros en la primera pasada.",
        )
        voltage = read_entry("voltage", "Tensión", 100)
        recovered = extraction(
            proposal(voltage),
            coverage("measurements", read_ids=["voltage"]),
            summary="Se recuperó la medida documentada.",
        )
        result, mocked = self._request([
            provider_response(found),
            provider_response(empty),
            provider_response(recovered),
        ])
        self.assertEqual(3, mocked.call_count)
        self.assertEqual(
            ["modulinkr_modbus_discovery", "modulinkr_modbus_catalog",
             "modulinkr_modbus_catalog"],
            [call.args[2]["text"]["format"]["name"]
             for call in mocked.call_args_list],
        )
        self.assertEqual(
            ["voltage"],
            [item["id"] for item in result["proposal"]["reads"]],
        )

    def test_incomplete_discovery_uses_the_only_recovery(self):
        incomplete = discovery(
            section("measurements", "measurement"),
            coverage_complete=False,
            unreviewed=["Tabla de registros de diagnóstico"],
        )
        found = discovery(
            section("measurements", "measurement"),
        )
        voltage = read_entry("voltage", "Tensión", 100)
        extracted = extraction(
            proposal(voltage),
            coverage("measurements", read_ids=["voltage"]),
        )
        result, mocked = self._request([
            provider_response(incomplete),
            provider_response(found),
            provider_response(extracted),
        ])
        self.assertEqual(3, mocked.call_count)
        self.assertEqual("voltage", result["proposal"]["reads"][0]["id"])

    def test_no_fourth_call_after_discovery_recovery(self):
        incomplete = discovery(
            section("measurements", "measurement"),
            coverage_complete=False,
            unreviewed=["Tabla incompleta"],
        )
        found = discovery(
            section("measurements", "measurement"),
        )
        empty = extraction(proposal(), coverage("measurements"))
        request = validated_request()
        with patch("modbus_ai_provider.post_responses", side_effect=[
                provider_response(incomplete),
                provider_response(found),
                provider_response(empty),
        ]) as mocked:
            with self.assertRaises(ProviderCallError):
                request_proposal(
                    CONFIG,
                    "offline-test-key",
                    request,
                    security_mode="production",
                )
        self.assertEqual(3, mocked.call_count)

    def test_confirmed_identity_mismatch_is_recovered_before_extraction(self):
        wrong = discovery(
            section("measurements", "measurement"),
        )
        wrong["identity"]["model"] = "Different 900"
        found = discovery(
            section("measurements", "measurement"),
        )
        voltage = read_entry("voltage", "Tensión", 100)
        extracted = extraction(
            proposal(voltage),
            coverage("measurements", read_ids=["voltage"]),
        )
        body = request_body()
        body["confirmed_identity"] = {
            "manufacturer": "Example Instruments",
            "model": "Meter 100",
            "revision": "1.0",
        }
        request = validate_assistant_request(body)
        with patch("modbus_ai_provider.post_responses", side_effect=[
                provider_response(wrong),
                provider_response(found),
                provider_response(extracted),
        ]) as mocked:
            result = request_proposal(
                CONFIG,
                "offline-test-key",
                request,
                security_mode="production",
            )
        self.assertEqual(3, mocked.call_count)
        self.assertEqual("voltage", result["proposal"]["reads"][0]["id"])

    def test_metadata_only_extraction_recovers_operational_section(self):
        found = discovery(
            section("measurements", "measurement", estimated_parameters=2),
            section("device_info", "metadata", estimated_parameters=2),
        )
        serial = read_entry(
            "serial", "Número de serie", 200,
            value_type="uint16", count=1, byte_order=None,
        )
        firmware = read_entry(
            "fw_ver", "Versión firmware", 201,
            value_type="uint16", count=1, byte_order=None,
        )
        initial = extraction(
            proposal(serial, firmware),
            coverage("measurements"),
            coverage("device_info", read_ids=["serial", "fw_ver"]),
        )
        voltage = read_entry("voltage", "Tensión", 100)
        current = read_entry("current", "Corriente", 102)
        recovered = extraction(
            proposal(serial, firmware, voltage, current),
            coverage("measurements", read_ids=["voltage", "current"]),
            coverage("device_info", read_ids=["serial", "fw_ver"]),
        )
        result, mocked = self._request([
            provider_response(found),
            provider_response(initial),
            provider_response(recovered),
        ])
        self.assertEqual(3, mocked.call_count)
        self.assertEqual(
            {"serial", "fw_ver", "voltage", "current"},
            {item["id"] for item in result["proposal"]["reads"]},
        )

    def test_failed_recovery_stops_after_the_third_call(self):
        found = discovery(
            section("measurements", "measurement", estimated_parameters=2),
        )
        empty = extraction(proposal(), coverage("measurements"))
        request = validated_request()
        with patch("modbus_ai_provider.post_responses", side_effect=[
                provider_response(found),
                provider_response(empty),
                provider_response(empty),
        ]) as mocked:
            with self.assertRaises(ProviderCallError):
                request_proposal(
                    CONFIG,
                    "offline-test-key",
                    request,
                    security_mode="production",
                )
        self.assertEqual(3, mocked.call_count)

    def test_transport_failure_is_not_retried_automatically(self):
        found = discovery(
            section("measurements", "measurement", estimated_parameters=1),
        )
        request = validated_request()
        transport_error = ProviderCallError(
            "No se pudo completar la consulta al proveedor.")
        with patch("modbus_ai_provider.post_responses", side_effect=[
                provider_response(found), transport_error,
        ]) as mocked:
            with self.assertRaises(ProviderCallError):
                request_proposal(
                    CONFIG,
                    "offline-test-key",
                    request,
                    security_mode="production",
                )
        self.assertEqual(2, mocked.call_count)

    def test_identity_source_keeps_web_search_in_both_stages(self):
        found = discovery(
            section("measurements", "measurement"), kind="web",
        )
        voltage = read_entry("voltage", "Tensión", 100)
        extracted = extraction(
            proposal(voltage, kind="web"),
            coverage("measurements", read_ids=["voltage"]),
        )
        result, mocked = self._request([
            provider_response(found), provider_response(extracted),
        ], kind="identity")
        self.assertEqual("voltage", result["proposal"]["reads"][0]["id"])
        self.assertEqual(2, mocked.call_count)
        for call in mocked.call_args_list:
            payload = call.args[2]
            self.assertEqual(
                [{"type": "web_search", "search_context_size": "high"}],
                payload["tools"],
            )

    def test_refinement_keeps_the_existing_single_call_flow(self):
        voltage = read_entry("voltage", "Tensión", 100)
        previous = proposal(voltage)
        result, mocked = self._request(
            [provider_response(previous)],
            previous=previous,
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


if __name__ == "__main__":
    unittest.main()
