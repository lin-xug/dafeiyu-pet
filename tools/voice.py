"""语音预设：一行命令切换声音风格。

    python tools/voice.py              # 看当前设置和所有预设
    python tools/voice.py 叛逆          # 切到叛逆版（参数全 1.0、无指令）
    python tools/voice.py 幼态          # 切到幼态版（慢 0.95 + 高 1.10 + 呆萌指令）

预设表在 `app/voice.py` 的 `PRESETS` 里，后台 /admin/voice 用的是同一份，别改两处。
改完要重启才生效：systemctl --user restart dafeiyu-bot
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, voice  # noqa: E402


def show() -> None:
    now = voice.current_preset()
    print("当前：")
    print(f"  voice    = {config.VOICE_NAME}")
    print(f"  rate     = {config.VOICE_RATE}")
    print(f"  pitch    = {config.VOICE_PITCH}")
    print(f"  instruct = {config.VOICE_INSTRUCT or '(空)'}")
    print(f"  预设     = {now or '(自定义，不在预设里)'}")
    print()
    print("预设：")
    for name, (rate, pitch, instruct) in voice.PRESETS.items():
        mark = " ←当前" if name == now else ""
        print(f"  {name:4s} rate={rate} pitch={pitch} instruct={instruct or '(空)'}{mark}")


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if not name:
        show()
        return
    if not voice.apply_preset(name):
        print(f"没有这个预设：{name}；可选：{'、'.join(voice.PRESETS)}")
        raise SystemExit(1)
    print(f"已切到「{name}」")
    print("重启生效：systemctl --user restart dafeiyu-bot")


if __name__ == "__main__":
    main()
