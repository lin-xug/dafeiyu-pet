"""把 QQ 里收藏的表情包导入到 `杂图/`。

QQ 的收藏表情存在本地缓存里：
  ~/.config/QQ/nt_qq_<hash>/nt_data/Emoji/personal_emoji/Ori/*.jpg

用法：
    python tools/import_qq_emoji.py              # 自动找所有账号的缓存
    python tools/import_qq_emoji.py --limit 20   # 只导 20 张
    python tools/import_qq_emoji.py --dry-run     # 看看会导什么
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "杂图"
EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def find_sources() -> list[Path]:
    return sorted(Path.home().glob(".config/QQ/nt_qq_*/nt_data/Emoji/personal_emoji/Ori"))


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 QQ 收藏表情")
    parser.add_argument("--src", action="append", default=[], help="指定目录，可多次")
    parser.add_argument("--dest", default=str(DEST))
    parser.add_argument("--limit", type=int, default=0, help="最多导多少张，0 = 全部")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    sources = [Path(p) for p in args.src] if args.src else find_sources()
    if not sources:
        print("没找到 QQ 的表情缓存目录，用 --src 手动指定")
        return

    existing = {md5_of(p) for p in dest.iterdir() if p.is_file() and p.suffix.lower() in EXTS}
    print(f"目标目录已有 {len(existing)} 张图")

    copied = skipped = 0
    for src in sources:
        files = sorted(p for p in src.iterdir() if p.suffix.lower() in EXTS)
        print(f"\n{src}\n  发现 {len(files)} 张")
        for path in files:
            if args.limit and copied >= args.limit:
                print(f"达到 {args.limit} 张上限，停")
                return
            digest = md5_of(path)
            if digest in existing:
                skipped += 1
                continue
            target = dest / f"qq_{digest[:10]}{path.suffix.lower()}"
            if args.dry_run:
                print(f"  [dry] {path.name} -> {target.name}")
            else:
                shutil.copy2(path, target)
            existing.add(digest)
            copied += 1

    print(f"\n导入 {copied} 张，跳过重复 {skipped} 张")
    if copied and not args.dry_run:
        print("下一步：python tools/tag_stickers.py")


if __name__ == "__main__":
    main()
