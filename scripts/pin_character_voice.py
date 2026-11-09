#!/usr/bin/env python
"""把一个「音色设计描述」固化成可复用音色（design → pin → clone）。

背景（2026-11-09 生产取证）：
``mimo-v2.5-tts-voicedesign`` 没有持久 voice_id —— 每次合成都按描述**现场
重新设计**，同一角色前后音色会漂。真机试听也证实：设计出来的声线明显比预置
音色「不那么 AI」，但直接绑 design 模型等于每句话换一把嗓子。

做法（试听已验证 E 路径）：先用描述合成一段参考音频 → 落盘 → 登记进
``data/voice_catalog.json``（kind=clone，带 sample_path）→ 角色绑定改成
``mimo-v2.5-tts-voiceclone`` + 该 voice_id。此后每次合成内联同一段参考音，
音色固定。

用法：
    # 预览（默认，不写任何文件）
    python scripts/pin_character_voice.py --character 7201e1ba \
        --description "二十出头的女生在微信里跟熟人说话，音高正常、不压嗓子…"

    # 真正落盘并绑定
    python scripts/pin_character_voice.py --character 7201e1ba \
        --description "…" --apply
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# CLI 里没有 main.py/run_api 的启动期加载：手工补一次 .env（MIMO_API_KEY）
# override=False —— 已存在的环境变量优先，不覆盖部署环境注入值。
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:  # noqa: BLE001 - 缺 python-dotenv 时仍可跑（env 已注入即可）
    pass

DEFAULT_REFERENCE_TEXT = (
    "嗯，我在呢。今天没什么特别的事，就是窗边那本书翻了两页，"
    "外面下雨了，雨声还挺好听的。你呢，忙完了没有？别又忘了吃饭。"
    "我啊，就那样吧，反正……你记得回我就行。"
)


def _rel(path: Path) -> str:
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:  # Windows 跨盘符
        return str(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="design → pin → clone 固化角色音色")
    ap.add_argument("--character", required=True, help="角色 ID（character_voices.json 的键）")
    ap.add_argument("--description", required=True, help="音色设计描述（自然语言）")
    ap.add_argument("--reference-text", default=DEFAULT_REFERENCE_TEXT,
                    help="生成参考音频用的文本（建议 10~20 秒口语）")
    ap.add_argument("--name", default="", help="catalog 显示名，默认按角色 ID 生成")
    ap.add_argument("--apply", action="store_true", help="真正落盘并绑定（默认只预览）")
    args = ap.parse_args()

    from utils.proactive_voice_delivery import get_tts_manager

    mgr = get_tts_manager()
    if mgr is None:
        print("[x] TTS 未启用或初始化失败（检查 config/system.yaml 的 voice.mimo-tts 与 MIMO_API_KEY）")
        return 2

    print(f"[1/4] 用 voicedesign 合成参考音（{len(args.reference_text)} 字）…")
    audio = asyncio.run(mgr.synthesize(
        args.reference_text,
        model="mimo-v2.5-tts-voicedesign",
        voice_id=args.description,
    ))
    if not audio or not getattr(audio, "data", None):
        print("[x] 参考音合成失败（模型返回空）。")
        return 3

    raw = audio.data
    fmt = str(getattr(audio, "fmt", "") or "mp3")
    digest = hashlib.sha256(args.description.encode("utf-8")).hexdigest()[:10]
    voice_id = f"pin_{args.character[:8]}_{digest}"
    sample_path = ROOT / "data" / "voice_samples" / f"{voice_id}.{fmt}"
    name = args.name or f"角色 {args.character} 固定音色"

    print(f"[2/4] 参考音 {len(raw)} 字节，格式 {fmt}")
    print(f"      voice_id   = {voice_id}")
    print(f"      sample     = {_rel(sample_path)}")
    print(f"[3/4] catalog 登记（kind=clone, model=mimo-v2.5-tts-voiceclone）")
    print(f"[4/4] 角色 {args.character} 绑定 → voiceclone + {voice_id}")

    if not args.apply:
        print("\n[预览] 未写入任何文件。确认后用 --apply 执行。")
        return 0

    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_bytes(raw)

    from voice.voice_catalog import get_voice_catalog

    get_voice_catalog().register(
        voice_id=voice_id,
        name=name,
        kind="clone",
        model="mimo-v2.5-tts-voiceclone",
        description=args.description,
        sample_path=_rel(sample_path),
        owner="",
    )

    from shisi.voice.character_voice import CharacterVoiceManager

    CharacterVoiceManager().bind_voice(
        args.character,
        engine="mimo-tts",
        speaker_name="",
        mimo_model="mimo-v2.5-tts-voiceclone",
        voice_id=voice_id,
        speed=1.0,
        pitch=1.0,
        pinned_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    print(f"\n[完成] {args.character} 已固定为 {voice_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
