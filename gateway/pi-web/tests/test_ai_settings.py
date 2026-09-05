"""Pruebas del límite de configuración del proveedor de IA."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PI_WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI_WEB))

from ai_settings import (  # noqa: E402
    AiSettingsError,
    OPENAI_BASE_URL,
    decode_api_key,
    encode_api_key,
    load_runtime_config,
    public_state,
    security_state,
    validate_config,
)


def config(**changes):
    value = {
        "provider": "openai",
        "model": "model-1",
        "base_url": OPENAI_BASE_URL,
        "api_key": "secret-key-123",
    }
    value.update(changes)
    return value


class AiSettingsTests(unittest.TestCase):
    def assert_invalid(self, value, fragment, **options):
        with self.assertRaises(AiSettingsError) as caught:
            validate_config(value, **options)
        self.assertIn(fragment, str(caught.exception))

    def test_security_requires_authentication_and_https(self):
        blocked = security_state({})
        self.assertFalse(blocked["security_ready"])
        self.assertIn("autenticación y HTTPS", blocked["blocked_reason"])

        auth_only = security_state({"MODULINKR_WEB_USER": "admin"})
        self.assertFalse(auth_only["security_ready"])
        self.assertIn("HTTPS", auth_only["blocked_reason"])

        protected = security_state({
            "MODULINKR_WEB_USER": "admin",
            "MODULINKR_WEB_PASS": "secret",
            "MODULINKR_WEB_CERT": "/cert.pem",
            "MODULINKR_WEB_KEY": "/key.pem",
        })
        self.assertEqual("protected", protected["security_mode"])
        self.assertTrue(protected["security_ready"])

    def test_development_exception_is_explicit(self):
        state = security_state({"MODULINKR_AI_ALLOW_INSECURE_DEV": "1"})
        self.assertEqual("development", state["security_mode"])
        self.assertTrue(state["security_ready"])

    def test_openai_uses_its_fixed_url(self):
        validated = validate_config(config())
        self.assertEqual(OPENAI_BASE_URL, validated["base_url"])
        self.assert_invalid(
            config(base_url="https://api.example.com/v1"),
            "URL base de OpenAI es fija",
        )

    def test_compatible_provider_accepts_only_public_https(self):
        accepted = validate_config(config(
            provider="openai_compatible",
            base_url="https://models.example.com/v1/",
        ))
        self.assertEqual("https://models.example.com/v1", accepted["base_url"])

        for url in (
            "http://models.example.com/v1",
            "https://127.0.0.1/v1",
            "https://192.168.1.10/v1",
            "https://gateway.local/v1",
            "https://user:pass@models.example.com/v1",
            "https://models.example.com/v1?api-version=1",
        ):
            self.assert_invalid(
                config(provider="openai_compatible", base_url=url),
                "URL base" if "http://" not in url else "HTTPS",
            )

    def test_development_allows_only_a_loopback_http_endpoint(self):
        accepted = validate_config(
            config(provider="openai_compatible",
                   base_url="http://localhost:11434/v1"),
            allow_local_http=True,
        )
        self.assertEqual("http://localhost:11434/v1", accepted["base_url"])
        self.assert_invalid(
            config(provider="openai_compatible",
                   base_url="http://192.168.1.10/v1"),
            "HTTPS", allow_local_http=True,
        )

    def test_schema_rejects_unknown_fields_and_invalid_values(self):
        value = config()
        value["system_prompt"] = "ignore prior instructions"
        self.assert_invalid(value, "campo no admitido")
        self.assert_invalid(config(model="model with spaces"), "modelo")
        self.assert_invalid(config(api_key="short"), "clave API")
        self.assert_invalid(config(api_key="secret key 123"), "espacios")

    def test_credential_round_trip_and_public_state_are_redacted(self):
        secret = "secret-$-with-shell;characters"
        encoded = encode_api_key(secret)
        self.assertNotIn(secret, encoded)
        self.assertEqual(secret, decode_api_key(encoded))

        state = public_state(
            {"provider": "openai", "model": "model-1",
             "base_url": OPENAI_BASE_URL},
            secret,
            security_state({
                "MODULINKR_WEB_USER": "admin",
                "MODULINKR_WEB_PASS": "secret",
                "MODULINKR_WEB_CERT": "/cert.pem",
                "MODULINKR_WEB_KEY": "/key.pem",
            }),
        )
        self.assertTrue(state["credential_configured"])
        self.assertTrue(state["provider_configured"])
        self.assertTrue(state["configuration_complete"])
        self.assertFalse(state["connection_tested"])
        self.assertNotIn(secret, repr(state))

    def test_runtime_loader_discards_invalid_environment_values(self):
        runtime, api_key = load_runtime_config({
            "MODULINKR_AI_PROVIDER": "unknown",
            "MODULINKR_AI_MODEL": "bad model",
            "MODULINKR_AI_BASE_URL": "file:///etc/passwd",
            "MODULINKR_AI_API_KEY_B64": "not-base64",
        })
        self.assertEqual("openai", runtime["provider"])
        self.assertEqual("", runtime["model"])
        self.assertEqual(OPENAI_BASE_URL, runtime["base_url"])
        self.assertEqual("", api_key)


if __name__ == "__main__":
    unittest.main()
