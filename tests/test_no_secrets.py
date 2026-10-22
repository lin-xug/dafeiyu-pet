"""守护测试：确保真实密钥永远不会混进仓库。

仓库是公开的，一旦把 .env 或带 sk- 的文件提交上去就是不可逆泄露。
这里把「扫描已跟踪文件有没有密钥」变成一条测试——以后误提交，测试会先红。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 允许出现在仓库里的占位符
PLACEHOLDERS = {
    "sk-your-key-here",
    "sk-xxxxxxxxxxxxxxxx",
    "sk-xxx",
}

# 高置信度的真实密钥形状：sk- 后跟 24 位以上字母数字
SECRET_RE = re.compile(r"sk-[A-Za-z0-9]{24,}")

# 这些扩展名不扫（二进制 / 长行日志）
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".silk", ".amr", ".mp3", ".ico"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "replace")
    return [ROOT / p for p in out.split("\0") if p]


def test_no_real_api_key_in_tracked_files() -> None:
    bad: list[str] = []
    for path in _tracked_files():
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in SECRET_RE.finditer(text):
            if m.group(0) in PLACEHOLDERS:
                continue
            line = text.count("\n", 0, m.start()) + 1
            bad.append(f"{path.relative_to(ROOT)}:{line} -> {m.group(0)[:12]}…")
    assert not bad, "疑似真实密钥被提交进仓库（立刻轮换并清理历史）：\n" + "\n".join(bad)


def test_env_is_not_tracked() -> None:
    tracked = {p.relative_to(ROOT).as_posix() for p in _tracked_files()}
    assert ".env" not in tracked, ".env 被跟踪了，真实密钥会随仓库公开泄露"
