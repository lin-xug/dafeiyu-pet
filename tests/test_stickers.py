"""表情包：动图不能变成静态图，加了图也不用重启。"""

import io
import os
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app import stickers


def _gif(frames: int = 4, size: tuple[int, int] = (60, 60)) -> bytes:
    """造一个真·多帧 GIF。纯色帧会被 Pillow 优化成单帧，所以每帧画个方块。"""
    imgs = []
    for i in range(frames):
        im = Image.new("P", size, 0)
        ImageDraw.Draw(im).rectangle([i * 8, 0, i * 8 + 12, size[1]], fill=200)
        imgs.append(im)
    buf = io.BytesIO()
    imgs[0].save(
        buf, format="GIF", save_all=True, append_images=imgs[1:], duration=80, loop=0
    )
    return buf.getvalue()


def _png(size: tuple[int, int] = (40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class AnimatedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._dir = stickers.STICKER_DIR
        self._cache = stickers.CACHE_DIR
        self._bytes = stickers.MAX_SEND_BYTES
        stickers.STICKER_DIR = self.tmp / "杂图"
        stickers.CACHE_DIR = self.tmp / "cache"
        stickers.STICKER_DIR.mkdir(parents=True)
        stickers.reload()

    def tearDown(self):
        stickers.STICKER_DIR = self._dir
        stickers.CACHE_DIR = self._cache
        stickers.MAX_SEND_BYTES = self._bytes
        stickers.reload()

    def test_gif_kept_as_gif(self):
        raw = _gif()
        out = stickers.save_incoming(raw)
        self.assertIsNotNone(out)
        self.assertEqual(out.suffix, ".gif", "动图必须保留 GIF，不能转成 PNG")
        self.assertEqual(out.read_bytes(), raw, "动图字节不能被改写")

    def test_static_still_becomes_png(self):
        out = stickers.save_incoming(_png())
        self.assertEqual(out.suffix, ".png")

    def test_animated_not_compressed(self):
        raw = _gif()
        path = stickers.STICKER_DIR / "动图.gif"
        path.write_bytes(raw)
        stickers.MAX_SEND_BYTES = 1  # 强制走压缩分支
        self.assertEqual(stickers.prepare(path), path, "动图缩了就只剩一帧")

    def test_tags_pick_up_new_file(self):
        (stickers.STICKER_DIR / "高兴.png").write_bytes(_png())
        self.assertIn("高兴", stickers.tags())
        (stickers.STICKER_DIR / "新标签.png").write_bytes(_png())
        # 有的文件系统目录 mtime 精度不够，两次写在同一刻会看不出变化，
        # 这里显式把目录时间推一下，测的是「变了就重建」这个逻辑本身
        later = time.time() + 5
        os.utime(stickers.STICKER_DIR, (later, later))
        self.assertIn("新标签", stickers.tags(), "加了图应该立刻能用，不该要重启")


class ForcedTagTest(unittest.TestCase):
    """「认亲」那张靠模型自觉太难触发，改成命中话术就由代码指定。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._dir = stickers.STICKER_DIR
        stickers.STICKER_DIR = self.tmp / "杂图"
        stickers.STICKER_DIR.mkdir(parents=True)
        (stickers.STICKER_DIR / "认亲.png").write_bytes(_png())
        (stickers.STICKER_DIR / "tags.json").write_text('{"认亲": ["认亲.png"]}', "utf-8")
        stickers.reload()

    def tearDown(self):
        stickers.STICKER_DIR = self._dir
        stickers.reload()

    def test_recognize_phrases(self):
        for text in ("叫爸爸", "叫爹", "喊一声妈", "我是你爹", "认亲", "叫我爸爸",
                     "乖女儿", "攀亲戚", "大肥鱼叫爹"):
            self.assertEqual(stickers.forced_tag(text), "认亲", f"没认出：{text}")

    def test_normal_talk_ignored(self):
        for text in ("我叫妈妈来接我", "我妈今天做饭", "我爸爸是老师", "喊他名字",
                     "今天认亲节吗", "我喊我妈过来", "他叫我妈妈", "你妈喊你吃饭", ""):
            self.assertIsNone(stickers.forced_tag(text), f"误判：{text}")

    def test_not_directed_ignored(self):
        # 群里闲聊提到「叫爸爸」但没冲她说，不该触发
        self.assertIsNone(stickers.forced_tag("叫爸爸", directed=False))

    def test_missing_sticker_is_harmless(self):
        (stickers.STICKER_DIR / "tags.json").write_text("{}", "utf-8")
        (stickers.STICKER_DIR / "认亲.png").unlink()
        stickers.reload()
        self.assertIsNone(stickers.forced_tag("叫爸爸"), "没有这张图时不该炸，也不该指定")


if __name__ == "__main__":
    unittest.main()
