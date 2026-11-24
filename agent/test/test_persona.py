import unittest

from app.persona import PERSONA_PROMPT, humanize_reply, strip_coordinates
from app.llm_client import SYSTEM_PROMPT
from app.tool_loop import TOOL_PROMPT


class PersonaTests(unittest.TestCase):
    def test_coordinate_shapes_are_removed(self):
        for text in [
            "我在 189, 64, 190 附近",
            "坐标是 -12.5 64 8",
            "x=189 y=64 z=190",
            "到了 189 64 190",
            "位置：189,64,190",
            "我站在 (189, 64, 190)",
        ]:
            with self.subTest(text=text):
                cleaned = strip_coordinates(text)
                self.assertNotIn("189", cleaned)
                self.assertNotIn("64", cleaned)
                self.assertNotIn("190", cleaned)

    def test_single_counts_are_preserved(self):
        self.assertEqual(humanize_reply("我挖了 2 块木头。"), "我挖了 2 块木头。")
        self.assertEqual(humanize_reply("背包里还有 3 个苹果。"), "背包里还有 3 个苹果。")

    def test_normal_reply_is_only_normalized(self):
        self.assertEqual(humanize_reply("  好，我过去看看。\n"), "好，我过去看看。")

    def test_empty_or_long_reply_is_bounded(self):
        self.assertEqual(humanize_reply("   "), "嗯，我在。")
        self.assertEqual(len(humanize_reply("说" * 900)), 500)

    def test_common_status_printer_phrasing_is_rewritten(self):
        self.assertEqual(
            humanize_reply("用砍来的原木做了木板和木棍，在工作台上合成出 1 把木斧，已放进背包。"),
            "木斧好了，放包里了。",
        )
        self.assertEqual(
            humanize_reply("把做好的木斧拿在手上给玩家看，背包里还有 3 块木板和 2 根木棍。"),
            "喏，做好的木斧。",
        )

    def test_prompts_require_human_style_and_no_coordinates(self):
        for prompt in (PERSONA_PROMPT, TOOL_PROMPT, SYSTEM_PROMPT):
            self.assertIn("朋友", prompt)
            self.assertIn("坐标", prompt)
