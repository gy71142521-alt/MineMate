"""模型工具视图与真实 Node 注册表的契约测试，不连接 Minecraft。"""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from app.orchestration.tools import skill_first_toolset
from app.tool_client import validate
from skills.catalog import create_skill_registry


@unittest.skipUnless(shutil.which("node"), "需要 Node.js 检查真实工具目录")
class SkillRoutingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        program = (
            "const {EventEmitter}=require('node:events');"
            "const {createToolSystem}=require('./src/tools');"
            "const r=createToolSystem(new EventEmitter());"
            "process.stdout.write(JSON.stringify(r.list()));r.dispose();"
        )
        output = subprocess.run(["node", "-e", program],
                                cwd=Path(__file__).resolve().parents[2] / "minecraft-bot",
                                capture_output=True, check=True, timeout=10)
        cls.node_tools = [{"type": "function", "function": definition}
                          for definition in json.loads(output.stdout)]
        cls.skills = create_skill_registry().list()

    def test_model_view_prefers_skills_without_changing_node_schemas(self):
        before = copy.deepcopy(self.node_tools)
        tools, routes = skill_first_toolset(self.node_tools, self.skills)
        visible = {item["function"]["name"]: item["function"] for item in tools}
        self.assertNotIn("craft_item", visible)
        self.assertNotIn("find_block", visible)
        self.assertEqual(routes["craft_item"], "craft_item")
        self.assertEqual(routes["find_block"], "explore_and_find")
        for name in ("place_block", "mine_block", "find_entity", "follow_player", "stop_movement",
                     "inspect_recipe", "furnace_add_input", "furnace_add_fuel", "furnace_take_output"):
            self.assertIn(name, visible)
        self.assertIn("gather_resource", visible["mine_block"]["description"])
        self.assertIn("smelt_item", visible["furnace_add_input"]["description"])
        args = {"position": {"x": 1, "y": 64, "z": 0}, "action": "status"}
        self.assertTrue(validate(visible["smelt_item"]["parameters"], args))
        self.assertTrue(validate(visible["smelt_item"]["parameters"], {**args, "action": "take"}))
        self.assertFalse(validate(visible["smelt_item"]["parameters"], {**args, "action": "start"}))
        self.assertEqual(self.node_tools, before)
        original = {item["function"]["name"]: item["function"] for item in self.node_tools}
        self.assertTrue(validate(original["smelt_item"]["parameters"], {**args, "action": "start"}))

    def test_missing_or_partial_registry_keeps_uncovered_tools(self):
        for skills in ([], [item for item in self.skills if item["name"] == "gather_resource"]):
            with self.subTest(skills=[item["name"] for item in skills]):
                tools, routes = skill_first_toolset(self.node_tools, skills)
                self.assertEqual(routes, {})
                visible = {item["function"]["name"]: item["function"] for item in tools}
                self.assertIn("craft_item", visible)
                self.assertIn("find_block", visible)
                self.assertIn("start", visible["smelt_item"]["parameters"]["properties"]["action"]["enum"])
                if not skills:
                    self.assertEqual(tools, self.node_tools)
