"""Comprueba aprobación explícita y rechazo de downgrade automático."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rak_firmware as rak


class RakPolicyTests(unittest.TestCase):
    def test_current_approved_version_is_accepted(self):
        result = rak.classify("RUI_4.3.0_RAK3172-E")
        self.assertEqual(result["state"], "approved")
        self.assertTrue(result["approved"])
        self.assertFalse(result["can_update"])

    def test_newer_unknown_version_is_never_updated_or_accepted(self):
        for version in ("4.3.1", "4.4.0", "5.0.0"):
            result = rak.classify(f"RUI_{version}_RAK3172-E")
            self.assertEqual(result["state"], "newer_unvalidated")
            self.assertFalse(result["approved"])
            self.assertFalse(result["can_update"])

    def test_newer_explicitly_approved_version_is_preserved(self):
        policy = rak.load_policy()
        policy["approved_versions"].append("4.4.0")
        result = rak.classify("RUI_4.4.0_RAK3172-E", policy)
        self.assertTrue(result["approved"])
        self.assertFalse(result["can_update"])

    def test_older_compatible_version_needs_update(self):
        for version in ("4.0.6", "4.2.0", "4.2.4"):
            self.assertEqual(rak.classify(f"RUI_{version}_RAK3172-E")["state"], "update_required")

    def test_wrong_model_old_unrecognized_and_malformed_versions_are_rejected(self):
        for value in ("RUI_4.3.0_RAK3172-T", "RUI_3.4.11_RAK3172-E", "", None,
                      "RUI_4.3.0_RAK3172-E\r\nOK", "RUI_4.3.0beta_RAK3172-E"):
            result = rak.classify(value)
            self.assertFalse(result["approved"])
            self.assertFalse(result["can_update"])


if __name__ == "__main__":
    unittest.main()
