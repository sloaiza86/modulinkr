"""Comprobaciones estructurales del diálogo conectado del asistente Modbus."""

from __future__ import annotations

import re
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


PI_WEB = Path(__file__).resolve().parents[1]
STATIC = PI_WEB / "static"


class MarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if attributes.get("id"):
            self.ids.append(attributes["id"])


class DialogStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.app = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.components = (STATIC / "components.js").read_text(encoding="utf-8")
        cls.styles = (STATIC / "style.css").read_text(encoding="utf-8")
        cls.parser = MarkupParser()
        cls.parser.feed(cls.html)

    def test_document_ids_remain_unique(self):
        duplicates = [name for name, count in Counter(self.parser.ids).items()
                      if count > 1]
        self.assertEqual([], duplicates)

    def test_device_button_opens_the_named_dialog_before_delete(self):
        assistant = self.app.index('class="fdev-ai"')
        delete = self.app.index('class="fdev-del"', assistant)
        self.assertLess(assistant, delete)
        self.assertIn('aria-controls="modbus-ai-dialog"',
                      self.app[assistant:delete])
        self.assertIn('aria-disabled="true"', self.app[assistant:delete])
        self.assertIn('getElementById("modbus-ai-assistant").open', self.app)

    def test_assistant_stays_disabled_until_settings_are_complete(self):
        self.assertIn('fetchApi("/api/ia/estado")', self.app)
        self.assertIn("modbusAiRefreshAvailability", self.app)
        self.assertIn("state?.configuration_complete && state?.security_ready",
                      self.app)
        self.assertIn('getAttribute("aria-disabled") === "true"', self.app)
        self.assertIn("El asistente de IA no está configurado", self.app)

    def test_pdf_picker_uses_the_gui_button_treatment(self):
        self.assertIn('class="mbai-file-input"', self.html)
        self.assertIn('class="mbai-file-button"', self.html)
        self.assertIn('id="mbai-file-name"', self.html)
        self.assertIn(".mbai-file-button", self.styles)
        self.assertIn(".mbai-file-input:focus-visible", self.styles)

    def test_dialog_has_four_steps_and_navigation(self):
        steps = re.findall(r'data-mbai-step="([1-4])"', self.html)
        progress = re.findall(r'data-mbai-progress="([1-4])"', self.html)
        self.assertEqual(["1", "2", "3", "4"], steps)
        self.assertEqual(["1", "2", "3", "4"], progress)
        for action in ("cancel", "back", "next", "apply-confirmed"):
            self.assertIn(f'data-mbai-action="{action}"', self.html)
        for action in ("manual-answer", "research-web"):
            self.assertNotIn(f'dataset.mbaiAction = "{action}"', self.components)

    def test_dialog_exposes_accessible_name_and_context(self):
        dialogs = [attrs for tag, attrs in self.parser.tags
                   if tag == "dialog" and attrs.get("id") == "modbus-ai-dialog"]
        self.assertEqual(1, len(dialogs))
        self.assertEqual("mbai-title", dialogs[0].get("aria-labelledby"))
        self.assertEqual("mbai-description", dialogs[0].get("aria-describedby"))
        self.assertIn('aria-live="polite"', self.html)

    def test_component_calls_the_validated_api_and_applies_through_the_form_adapter(self):
        self.assertNotIn("Demostración local", self.html)
        self.assertNotIn("No analiza archivos, no navega y no modifica el formulario.",
                         self.html)
        start = self.components.index("class ModuLinkrModbusAiAssistant")
        end = self.components.index("class ModuLinkrOverlay", start)
        component = self.components[start:end]
        self.assertIn('"/api/ia/modbus/proponer"', component)
        self.assertIn('this.emit("modulinkr-modbus-ai-apply"', component)
        self.assertIn('"modulinkr-modbus-ai-apply"', self.app)
        self.assertIn("modbusAiApplyProposal", self.app)
        self.assertIn("modbusAiMarkPending", self.app)
        self.assertIn("formLive();", self.app)

    def test_candidates_and_final_review_are_dynamic_and_safely_rendered(self):
        self.assertIn('id="mbai-candidates-container"', self.html)
        self.assertIn('id="mbai-review-items"', self.html)
        self.assertIn('id="mbai-review-correction-list"', self.html)
        self.assertIn('id="mbai-review-excluded-list"', self.html)
        start = self.components.index("class ModuLinkrModbusAiAssistant")
        end = self.components.index("class ModuLinkrOverlay", start)
        component = self.components[start:end]
        self.assertIn("document.createElement", component)
        self.assertIn("textContent = copy.question", component)
        self.assertIn("this._webQueries = new Set((selection.pending || [])", component)
        self.assertIn("answers: []", component)
        self.assertNotIn("innerHTML", component)

    def test_initial_catalog_is_guarded_before_it_changes_dialog_state(self):
        start = self.components.index("class ModuLinkrModbusAiAssistant")
        end = self.components.index("class ModuLinkrOverlay", start)
        component = self.components[start:end]
        guard = component.index("this._assertProposalResponse(data, !previous);")
        assignment = component.index("this._proposal = data.proposal;", guard)
        self.assertLess(guard, assignment)
        self.assertIn("proposal.reads.length + proposal.writes.length > 0",
                      component)
        self.assertIn("No se obtuvo un catálogo Modbus fiable", component)
        self.assertNotIn(
            "Revisa las preguntas y fuentes devueltas por el proveedor",
            component,
        )

    def test_changed_identity_is_reanalysed_before_candidates_are_rendered(self):
        start = self.components.index("class ModuLinkrModbusAiAssistant")
        end = self.components.index("class ModuLinkrOverlay", start)
        component = self.components[start:end]
        step = component.index('if (this._paso === 2) {')
        render = component.index("this._renderCandidates();", step)
        fragment = component[step:render]
        self.assertIn("this._identityChanged()", fragment)
        self.assertIn("await this._solicitarPropuesta(null, identity);", fragment)
        self.assertIn("confirmed_identity: confirmedIdentity === undefined",
                      component)

    def test_only_reads_or_writes_can_be_applied(self):
        start = self.components.index("_hasApplicableChanges(proposal)")
        end = self.components.index("_loadablePending()", start)
        guard = self.components[start:end]
        self.assertIn("proposal?.reads", guard)
        self.assertIn("proposal?.writes", guard)
        for metadata in ("name", "description", "change_function",
                         "change_address", "read_mode", "inter_read_ms"):
            self.assertNotIn(f'"{metadata}"', guard)

    def test_assistant_copy_has_no_development_or_simulator_messages(self):
        html_start = self.html.index('<modulinkr-modbus-ai-assistant')
        html_end = self.html.index('</modulinkr-modbus-ai-assistant>', html_start)
        assistant = (self.html[html_start:html_end] + self.components).casefold()
        for forbidden in (
            "demostración local", "modo local de desarrollo",
            "configuración temporal guardada", "se eliminará al detener el simulador",
            "el firmware todavía no las ejecuta",
        ):
            self.assertNotIn(forbidden, assistant)

    def test_assets_share_the_dialog_cache_version(self):
        version = "modbus-ai-guard-20260824"
        self.assertEqual(3, self.html.count(version))
        self.assertIn(".mbai-dialog", self.styles)
        self.assertIn('"modulinkr-modbus-ai-assistant"', self.components)


if __name__ == "__main__":
    unittest.main()
