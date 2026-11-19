import unittest

from app.semantic import analyze_message, canonical_name, normalize_arguments


class SemanticTests(unittest.TestCase):
    def test_only_explicit_ids_are_canonicalized(self):
        self.assertEqual(canonical_name("minecraft:iron_helmet"), "iron_helmet")
        self.assertIsNone(canonical_name("铁头盔"))
        self.assertIsNone(canonical_name("unknown item"))

    def test_message_analysis_does_not_guess_resources(self):
        result = analyze_message("帮我炼铁和挖一个模组矿")
        self.assertTrue(result["requires_runtime_resolution"])
        self.assertEqual(result["resource_terms"], [])

    def test_explicit_id_is_only_reported_as_a_hint(self):
        result = analyze_message("craft iron_helmet")
        self.assertEqual(result["resource_terms"], ["craft", "iron_helmet"])

    def test_normalization_is_identity(self):
        arguments = {"item_name": "铁头盔"}
        self.assertEqual(normalize_arguments("craft_item", arguments), arguments)
