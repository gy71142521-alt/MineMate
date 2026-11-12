import unittest

from app.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        settings = load_settings({})
        self.assertEqual(settings.node_api_url, "http://127.0.0.1:3000")
        self.assertEqual(settings.agent_port, 8000)

    def test_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            load_settings({"NODE_API_URL": "ftp://bad"})
        with self.assertRaises(ValueError):
            load_settings({"NODE_API_TIMEOUT_SECONDS": "0"})
        with self.assertRaises(ValueError):
            load_settings({"AGENT_PORT": "70000"})

    def test_llm_configuration(self):
        settings = load_settings({"LLM_API_KEY": "unit-secret", "LLM_MODEL": "mock", "LLM_TIMEOUT": "15"})
        self.assertEqual(settings.llm_timeout, 15)
        self.assertEqual(settings.llm_api_key, "unit-secret")
        self.assertNotIn("unit-secret", repr(settings))
        for name, value in [("LLM_BASE_URL", "https://unit-secret@example.com/v1"), ("LLM_BASE_URL", "https://example.com?key=unit-secret"),
                            ("LLM_TIMEOUT", "nan"), ("LLM_TIMEOUT", "0"), ("LLM_MAX_CONCURRENT", "1.5"),
                            ("LLM_REQUESTS_PER_MINUTE", "121"), ("LLM_PLAYER_COOLDOWN", "0")]:
            with self.assertRaises(ValueError):
                load_settings({name: value})

    def test_tool_configuration_is_opt_in_and_bounded(self):
        self.assertFalse(load_settings({}).tool_calling_enabled)
        settings = load_settings({"TOOL_CALLING_ENABLED": "true", "TOOL_MAX_CALLS": "3", "TOOL_CALLING_TIMEOUT": "60", "TOOL_API_TIMEOUT": "25"})
        self.assertTrue(settings.tool_calling_enabled)
        self.assertEqual(settings.tool_max_calls, 3)
        for name, value in [("TOOL_CALLING_ENABLED", "yes"), ("TOOL_MAX_CALLS", "0"), ("TOOL_MAX_CALLS", "9"), ("TOOL_MAX_CALLS", "1.5"), ("TOOL_CALLING_TIMEOUT", "nan"), ("TOOL_API_TIMEOUT", "61")]:
            with self.assertRaises(ValueError):
                load_settings({name: value})

    def test_tool_output_budget_is_independent_and_bounded(self):
        self.assertEqual(load_settings({}).tool_llm_max_tokens, 2048)
        self.assertEqual(load_settings({"TOOL_LLM_MAX_TOKENS": "4096"}).tool_llm_max_tokens, 4096)
        for value in ("0", "-1", "1.5", "8193", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_settings({"TOOL_LLM_MAX_TOKENS": value})
