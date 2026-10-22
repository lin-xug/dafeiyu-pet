import shutil
import unittest

from app import config, playbook

GID = "unit-playbook"


class PlaybookTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(config.GMEMORY_DIR / GID, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(config.GMEMORY_DIR / GID, ignore_errors=True)

    def test_add_dedupe(self):
        self.assertTrue(playbook.add(GID, "梗", "输了要请奶茶"))
        self.assertFalse(playbook.add(GID, "梗", "输了要请奶茶"))
        self.assertEqual(len(playbook.active(GID)), 1)

    def test_similar_merge(self):
        playbook.add(GID, "梗", "输了要请奶茶")
        playbook.add(GID, "梗", "输了请奶茶")
        self.assertEqual(len(playbook.active(GID)), 1)

    def test_update_delete(self):
        playbook.add(GID, "关系", "甲爱怼乙")
        item = playbook.active(GID)[0]
        self.assertTrue(playbook.update(GID, item["id"], "甲和乙互怼"))
        self.assertEqual(playbook.active(GID)[0]["text"], "甲和乙互怼")
        self.assertTrue(playbook.delete(GID, item["id"]))
        self.assertEqual(playbook.active(GID), [])

    def test_render_relevance(self):
        playbook.add(GID, "梗", "输了要请奶茶")
        playbook.add(GID, "坑", "提原神会被围攻")
        text = playbook.render(GID, limit=1, query="奶茶呢")
        self.assertIn("奶茶", text)

    def test_prune(self):
        for i in range(playbook.MAX_ITEMS + 10):
            playbook.add(GID, "事", f"事件{i}号")
        playbook.prune(GID)
        self.assertLessEqual(len(playbook.load(GID)["items"]), playbook.MAX_ITEMS)

    def test_seed(self):
        playbook.seed_from_summary(GID, "以前的一段摘要")
        self.assertEqual(len(playbook.active(GID)), 1)


if __name__ == "__main__":
    unittest.main()
