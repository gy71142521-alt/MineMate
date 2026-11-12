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
