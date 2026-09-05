"""Pruebas del adaptador de proveedor del asistente Modbus."""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PI_WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI_WEB))

from modbus_ai_provider import (  # noqa: E402
    AssistantRequestError,
    SYSTEM_PROMPT,
    build_provider_payload,
    count_response_input_tokens,
    parse_provider_response,
    validate_assistant_request,
)


def evidence(source_id="manual-1"):
    return {
        "source_id": source_id,
        "page": 12,
        "section": "Register map",
        "excerpt": "Input register 0 contains temperature as int16 x 0.1.",
    }


def proposal(pending=False):
    result = {
        "contract_version": "1.2",
        "sources": [{
            "id": "manual-1", "kind": "manual",
            "title": "Example manual", "url": None,
        }],
        "identity": {
            "manufacturer": "Example", "model": "T100", "revision": None,
            "evidence": [evidence()],
        },
        "bus": {
            "baudrate": 9600, "parity": "N", "stopbits": 1,
            "evidence": [evidence()],
        },
        "device": {
            "name": "t100", "description": "Temperature sensor",
            "default_slave_id": 1, "desired_slave_id": 1,
            "change_function": None, "change_address": None,
            "read_mode": "grouped", "inter_read_ms": 250,
            "evidence": [evidence()],
        },
        "reads": [{
            "id": "temp", "name": "Temperatura",
            "function": "read_input_registers", "address": 0, "count": 1,
            "type": "int16", "byte_order": None, "scale": 0.1,
            "offset": 0, "unit": "C", "evidence": [evidence()],
        }],
        "writes": [],
        "pending": [],
        "unsupported": [],
    }
    if pending:
        result["reads"][0]["scale"] = None
        result["pending"] = [{
            "scope": "read", "field": "reads.temp.scale",
            "question": "¿Cuál es la escala?",
            "reason": "El extracto no la confirma.",
            "can_research_web": True,
            "web_query": "Example T100 Modbus temperature scale",
            "evidence": [evidence()],
        }]
    return result


def request_body():
    return {
        "operation": "discover",
        "source": {
            "kind": "identity", "manufacturer": "Example",
            "model": "T100", "filename": None, "pdf_base64": None,
        },
        "confirmed_identity": None,
        "current": {"bus": {"baudrate": 9600}, "device": {"name": ""}},
        "discovery": None,
        "target_id": None,
        "selected_sections": [],
        "previous_proposal": None,
        "selected": {"reads": [], "writes": []},
        "answers": [],
        "web_queries": [],
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


class ProviderAdapterTests(unittest.TestCase):
    def test_refinement_fallback_search_drops_the_exact_model(self):
        self.assertIn(
            "la segunda y última búsqueda debe omitir el modelo exacto",
            SYSTEM_PROMPT,
        )
        self.assertIn(
            "Una trama de solicitud Modbus del maestro",
            SYSTEM_PROMPT,
        )

    def test_input_count_omits_generation_and_tool_call_limits(self):
        payload = {
            "model": "gpt-5.6",
            "store": False,
            "max_output_tokens": 18000,
            "max_tool_calls": 1,
            "input": "test",
        }
        with patch(
                "modbus_ai_provider._post_provider_json",
                return_value={"input_tokens": 7}) as mocked:
            result = count_response_input_tokens(
                "https://api.openai.com/v1", "offline-test-key", payload)

        self.assertEqual(7, result)
        counted = mocked.call_args.args[2]
        self.assertNotIn("store", counted)
        self.assertNotIn("max_output_tokens", counted)
        self.assertNotIn("max_tool_calls", counted)

    def test_identity_request_enables_web_and_builds_strict_responses_payload(self):
        request = validate_assistant_request(request_body())
        payload = build_provider_payload(request, "gpt-5.6")
        self.assertTrue(request["use_web"])
        self.assertEqual([{"type": "web_search", "search_context_size": "high"}],
                         payload["tools"])
        self.assertEqual(8, payload["max_tool_calls"])
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("$schema", payload["text"]["format"]["schema"])

    def test_identity_accepts_model_only_and_ignores_unknown_manufacturer(self):
        for manufacturer in (None, "", "Generico", "Genérico", "Unknown"):
            with self.subTest(manufacturer=manufacturer):
                body = request_body()
                body["source"].update({
                    "manufacturer": manufacturer,
                    "model": "XY-MD02",
                })
                request = validate_assistant_request(body)
                self.assertIsNone(request["source"]["manufacturer"])
                self.assertEqual("XY-MD02", request["source"]["model"])

    def test_identity_still_requires_the_model(self):
        body = request_body()
        body["source"].update({"manufacturer": "Example", "model": ""})
        with self.assertRaisesRegex(AssistantRequestError, "modelo es obligatorio"):
            validate_assistant_request(body)

    def test_manual_must_be_a_real_pdf_with_bounded_base64(self):
        body = request_body()
        body["source"] = {
            "kind": "manual", "manufacturer": None, "model": None,
            "filename": "manual.pdf",
            "pdf_base64": base64.b64encode(b"not a pdf").decode("ascii"),
        }
        with self.assertRaisesRegex(AssistantRequestError, "no es un PDF"):
            validate_assistant_request(body)

        body["source"]["pdf_base64"] = base64.b64encode(
            b"%PDF-1.7\nexample").decode("ascii")
        request = validate_assistant_request(body)
        payload = build_provider_payload(request, "gpt-5.6")
        content = payload["input"][1]["content"]
        self.assertEqual("input_file", content[1]["type"])
        self.assertNotIn("tools", payload)

    def test_refinement_does_not_retransmit_the_manual_pdf(self):
        body = request_body()
        body["operation"] = "refine"
        body["source"] = {
            "kind": "manual", "manufacturer": None, "model": None,
            "filename": "manual.pdf",
            "pdf_base64": base64.b64encode(
                b"%PDF-1.7\nexample").decode("ascii"),
        }
        body["previous_proposal"] = proposal(pending=True)
        body["selected"] = {"reads": ["temp"], "writes": []}
        body["web_queries"] = [
            "Example T100 Modbus temperature scale",
        ]
        request = validate_assistant_request(body)
        payload = build_provider_payload(request, "gpt-5.6")
        content = payload["input"][1]["content"]

        self.assertFalse(any(
            item.get("type") == "input_file" for item in content
        ))
        self.assertNotIn("pdf_base64", content[0]["text"])
        self.assertIn("búsquedas obligatorias", content[0]["text"])
        self.assertIn(
            "No sustituyas estas búsquedas por una consulta general",
            content[0]["text"],
        )
        self.assertIn(
            "cada URL web citada como evidencia",
            content[0]["text"],
        )
        self.assertEqual(8, payload["max_tool_calls"])

    def test_refinement_reserves_an_open_page_for_each_global_query(self):
        body = request_body()
        body["operation"] = "refine"
        previous = proposal()
        previous["reads"][0]["function"] = None
        previous["reads"][0]["address"] = None
        previous["reads"][0]["scale"] = None
        queries = [
            "Example T100 Modbus function",
            "Example T100 Modbus address",
            "Example T100 Modbus scale",
        ]
        previous["pending"] = [
            {
                "scope": "read",
                "field": f"reads.temp.{field}",
                "question": "¿Cuál es el valor?",
                "reason": "El extracto no lo confirma.",
                "can_research_web": True,
                "web_query": query,
                "evidence": [evidence()],
            }
            for field, query in zip(("function", "address", "scale"), queries)
        ]
        body["previous_proposal"] = previous
        body["selected"] = {"reads": ["temp"], "writes": []}
        body["web_queries"] = []

        request = validate_assistant_request(body)
        payload = build_provider_payload(request, "gpt-5.6")

        self.assertEqual(3, len(request["web_queries"]))
        self.assertEqual(12, payload["max_tool_calls"])

    def test_refinement_prompt_contains_only_the_selected_catalog_entries(self):
        body = request_body()
        body["operation"] = "refine"
        previous = proposal(pending=True)
        previous["reads"].append({
            "id": "other", "name": "Otro valor",
            "function": "read_input_registers", "address": 2, "count": 1,
            "type": "uint16", "byte_order": None, "scale": None,
            "offset": None, "unit": None, "evidence": [evidence()],
        })
        previous["unsupported"].append({
            "category": "other",
            "summary": "Elemento previo no relacionado",
            "reason": "No forma parte del refinamiento seleccionado.",
            "evidence": [evidence()],
        })
        body["previous_proposal"] = previous
        body["selected"] = {"reads": ["temp"], "writes": []}
        body["web_queries"] = [
            "Example T100 Modbus temperature scale",
        ]
        request = validate_assistant_request(body)

        payload = build_provider_payload(request, "gpt-5.6")
        text = payload["input"][1]["content"][0]["text"]

        self.assertIn('"id":"temp"', text)
        self.assertNotIn('"id":"other"', text)
        self.assertNotIn("Elemento previo no relacionado", text)

    def test_refinement_accepts_only_declared_questions_and_queries(self):
        body = request_body()
        body["operation"] = "refine"
        body["previous_proposal"] = proposal(pending=True)
        body["selected"] = {"reads": ["temp"], "writes": []}
        body["answers"] = [{"field": "reads.temp.scale", "answer": "0.1"}]
        body["web_queries"] = ["Example T100 Modbus temperature scale"]
        request = validate_assistant_request(body)
        self.assertTrue(request["use_web"])

        body["web_queries"] = ["unrelated private network target"]
        with self.assertRaisesRegex(AssistantRequestError, "búsqueda no propuesta"):
            validate_assistant_request(body)

    def test_current_rejects_non_finite_numbers(self):
        body = request_body()
        body["current"]["bus"]["baudrate"] = float("nan")
        with self.assertRaisesRegex(AssistantRequestError, "número no finito"):
            validate_assistant_request(body)

    def test_provider_output_is_parsed_and_validated_again(self):
        result = parse_provider_response(provider_response(proposal()))
        self.assertTrue(result["ready"])
        self.assertEqual("temp", result["proposal"]["reads"][0]["id"])

    def test_direct_request_frame_resolves_address_before_reference_notation(self):
        raw = proposal()
        raw["reads"][0]["address"] = None
        raw["reads"][0]["evidence"] = [{
            "source_id": "manual-1",
            "page": 3,
            "section": "Master request frame",
            "excerpt": (
                "A secondary display calls this register 30001. "
                "Master command frame: 01 04 00 01 00 01 60 0A."
            ),
        }]
        raw["pending"] = [{
            "scope": "read", "field": "reads.temp.address",
            "question": "¿Cuál es la dirección PDU?",
            "reason": "Las referencias usan bases diferentes.",
            "can_research_web": False, "web_query": None,
            "evidence": raw["reads"][0]["evidence"],
        }]

        result = parse_provider_response(provider_response(raw))

        self.assertTrue(result["ready"])
        self.assertEqual(1, result["proposal"]["reads"][0]["address"])
        self.assertEqual([], result["proposal"]["pending"])

    def test_pending_address_research_works_without_manufacturer(self):
        body = request_body()
        body["operation"] = "refine"
        previous = proposal()
        previous["identity"]["manufacturer"] = None
        previous["identity"]["model"] = "Sensor-200"
        previous["reads"][0]["address"] = None
        previous["pending"] = [{
            "scope": "read", "field": "reads.temp.address",
            "question": "¿Cuál es la dirección PDU?",
            "reason": "La referencia no demuestra la dirección transmitida.",
            "can_research_web": False, "web_query": None,
            "evidence": [evidence()],
        }]
        body["source"].update({"manufacturer": None, "model": "Sensor-200"})
        body["previous_proposal"] = previous
        body["selected"] = {"reads": ["temp"], "writes": []}

        request = validate_assistant_request(body)

        self.assertEqual(1, len(request["web_queries"]))
        self.assertIn("Sensor-200", request["web_queries"][0])
        self.assertIn('"master command frame"', request["web_queries"][0])

    def test_fc16_with_one_register_keeps_the_documented_function(self):
        raw = proposal()
        raw["writes"] = [{
            "id": "target", "name": "Consigna",
            "function": "write_multiple_registers", "purpose": "operational",
            "address": 20, "count": 1, "type": "int16", "byte_order": None,
            "scale": 1, "offset": 0, "unit": "C", "evidence": [evidence()],
        }]
        result = parse_provider_response(provider_response(raw))
        self.assertEqual(
            "write_multiple_registers",
            result["proposal"]["writes"][0]["function"],
        )

    def test_unanchored_write_placeholder_is_excluded(self):
        raw = proposal()
        raw["writes"] = [{
            "id": "unknown", "name": "Escritura sin registro",
            "function": "write_multiple_registers", "purpose": "operational",
            "address": None, "count": None, "type": None,
            "byte_order": None, "scale": None, "offset": None,
            "unit": None, "evidence": [evidence()],
        }]
        raw["pending"] = [{
            "scope": "write", "field": "writes.unknown.address",
            "question": "¿Cuál es la dirección?",
            "reason": "La fuente no documenta una escritura operativa.",
            "can_research_web": False, "web_query": None,
            "evidence": [evidence()],
        }]

        result = parse_provider_response(provider_response(raw))

        self.assertEqual([], result["proposal"]["writes"])
        self.assertEqual([], result["proposal"]["pending"])
        self.assertTrue(any(
            "estructura técnica suficiente" in item["reason"]
            for item in result["proposal"]["unsupported"]
        ))

    def test_incompatible_scalar_shape_is_excluded_without_coercion(self):
        raw = proposal()
        raw["reads"][0]["count"] = 2
        result = parse_provider_response(provider_response(raw))
        self.assertEqual([], result["proposal"]["reads"])
        self.assertEqual(
            ["data_shape"],
            [item["category"] for item in result["proposal"]["unsupported"]],
        )

    def test_commissioning_write_and_device_bus_do_not_change_the_line(self):
        raw = proposal()
        raw["writes"] = [{
            "id": "baud", "name": "Velocidad de comunicación",
            "function": "write_multiple_registers", "purpose": "commissioning",
            "address": 28, "count": 1, "type": "uint16", "byte_order": None,
            "scale": 1, "offset": 0, "unit": None, "evidence": [evidence()],
        }]
        current = {
            "current": {
                "bus": {"baudrate": 19200, "parity": "E", "stopbits": 1},
                "device": {"default_slave_id": 7, "desired_slave_id": 7},
            },
        }
        result = parse_provider_response(provider_response(raw), current)
        self.assertEqual(
            {"baudrate": None, "parity": None, "stopbits": None,
             "evidence": [evidence()]},
            result["proposal"]["bus"],
        )
        self.assertEqual([], result["proposal"]["writes"])
        self.assertEqual(7, result["proposal"]["device"]["default_slave_id"])
        self.assertEqual(
            {"bus_conflict", "communication"},
            {item["category"] for item in result["proposal"]["unsupported"]},
        )


if __name__ == "__main__":
    unittest.main()
