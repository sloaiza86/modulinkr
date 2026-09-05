"""Comprobaciones estructurales de la configuración de IA."""

from __future__ import annotations

import unittest
from pathlib import Path


PI_WEB = Path(__file__).resolve().parents[1]
GATEWAY = PI_WEB.parent
STATIC = PI_WEB / "static"


class AiSettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.app = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.service = (PI_WEB / "web_service.py").read_text(encoding="utf-8")
        cls.api = (PI_WEB / "aiapi.py").read_text(encoding="utf-8")
        cls.setter = (GATEWAY / "pi-service" / "set_ai.sh").read_text(
            encoding="utf-8")
        cls.installer = (GATEWAY / "pi-service" / "installer" / "lib"
                         / "web.sh").read_text(encoding="utf-8")

    def test_settings_menu_routes_to_ai_panel(self):
        self.assertIn('href="#/configuracion/ia"', self.html)
        self.assertIn('id="cfg-ia"', self.html)
        self.assertIn('"ia":            { panel: "cfg-ia"', self.app)
        self.assertIn('if (sub === "ia") iaCargar()', self.app)

    def test_credential_field_never_contains_a_saved_value(self):
        self.assertIn('id="ia-api-key" type="password"', self.html)
        self.assertIn('autocomplete="new-password"', self.html)
        self.assertNotIn('value="sk-', self.html)
        self.assertIn('apiKey.value = ""', self.app)

    def test_ui_tests_connection_while_saving(self):
        self.assertIn("Al guardar se hará una solicitud mínima", self.html)
        self.assertIn("Comprobar y guardar", self.html)
        self.assertIn("Comprobando el proveedor", self.app)
        self.assertIn("Proveedor verificado", self.app)
        self.assertNotIn("/api/ia/probar", self.app)

    def test_saved_state_immediately_updates_assistant_availability(self):
        self.assertIn("modbusAiAvailabilityFromState(data)", self.app)
        self.assertIn("configuration_complete", self.app)

    def test_ui_does_not_expose_local_runtime_messages(self):
        self.assertNotIn("se conserva solo en memoria", self.app)
        self.assertNotIn("Modo local de desarrollo", self.app)
        self.assertNotIn("Configuración temporal", self.app)

    def test_router_is_authenticated_and_exposes_settings_and_modbus_actions(self):
        self.assertIn("import aiapi", self.service)
        self.assertIn("app.include_router(aiapi.router, dependencies=[Depends(require_auth)])",
                      self.service)
        self.assertIn('router = APIRouter(prefix="/api/ia")', self.api)
        self.assertIn('@router.get("/estado")', self.api)
        self.assertIn('@router.post("/guardar")', self.api)
        self.assertIn("test_provider_connection", self.api)
        self.assertIn('@router.post("/modbus/proponer")', self.api)
        self.assertIn('@router.post("/modbus/validar")', self.api)
        self.assertNotIn('@router.post("/probar")', self.api)

    def test_privileged_writer_has_a_closed_allowlist(self):
        for key in (
            "MODULINKR_AI_PROVIDER",
            "MODULINKR_AI_MODEL",
            "MODULINKR_AI_BASE_URL",
            "MODULINKR_AI_API_KEY_B64",
            "MODULINKR_AI_VERIFIED_SHA256",
        ):
            self.assertIn(key, self.setter)
        self.assertIn('input=stdin', self.api)
        self.assertIn('"$APP_DIR/set_ai.sh"', self.installer)
        self.assertIn('$APP_DIR/set_ai.sh,', self.installer)
        self.assertIn("MODULINKR_AI_VERIFIED_SHA256", self.installer)

    def test_security_gate_and_redacted_status_are_visible_in_source(self):
        self.assertIn('if not security["security_ready"]', self.api)
        self.assertIn('credential_configured', self.app)
        self.assertNotIn('data.api_key', self.app)
        self.assertIn('connection_tested', (PI_WEB / "ai_settings.py").read_text(
            encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
