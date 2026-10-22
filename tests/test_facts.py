import json
import unittest

from app import config, facts


class FactsTest(unittest.TestCase):
    def test_parse(self):
        raw = "- [长期] 养了只猫叫豆豆\n- [临时] 下个月去成都\n乱七八糟"
        self.assertEqual(
            facts.parse_facts(raw), [("养了只猫叫豆豆", "长期"), ("下个月去成都", "临时")]
        )

    def test_add_and_dedupe(self):
        uid = "unit-facts-1"
        self.assertTrue(facts.add(uid, "养了只猫叫豆豆", "长期"))
        self.assertFalse(facts.add(uid, "养了只猫叫豆豆", "长期"))
        self.assertIn("养了只猫叫豆豆", facts.active(uid))

    def test_expiry(self):
        uid = "unit-facts-2"
        facts.add(uid, "明天考试", "临时")
        rows = json.loads((config.FACTS_DIR / f"{uid}.json").read_text("utf-8"))
        self.assertIsNotNone(rows["facts"][0]["expire"])

    def test_block(self):
        uid = "unit-facts-3"
        facts.add(uid, "在成都工作", "长期")
        self.assertIn("成都", facts.block(uid))


if __name__ == "__main__":
    unittest.main()
