"""Pruebas del adaptador de proveedor del asistente Modbus."""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path


PI_WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI_WEB))

from modbus_ai_provider import (  # noqa: E402
    AssistantRequestError,
    build_provider_payload,
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
        "source": {
            "kind": "identity", "manufacturer": "Example",
            "model": "T100", "filename": None, "pdf_base64": None,
        },
        "confirmed_identity": None,
        "current": {"bus": {"baudrate": 9600}, "device": {"name": ""}},
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
    def test_identity_request_enables_web_and_builds_strict_responses_payload(self):
        request = validate_assistant_request(request_body())
        payload = build_provider_payload(request, "gpt-5.6")
        self.assertTrue(request["use_web"])
        self.assertEqual([{"type": "web_search", "search_context_size": "high"}],
                         payload["tools"])
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("$schema", payload["text"]["format"]["schema"])

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

    def test_refinement_accepts_only_declared_questions_and_queries(self):
        body = request_body()
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
