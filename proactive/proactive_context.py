"""主动消息上下文统一真源 — 决策线（llm_proactive）与生成线（ase_engine）共用。

2026-10-15 质量回调批：此前两条主动线各自拼 prompt、各自截断人设、各自投影
画像（无 preferences）、各自检测否认称呼（单源）——同一概念多个 owner。
本模块收敛：完整版人设加载、沉浸式约束渲染。画像唯一读入口 =
shisi.agent_plane.runtime.get_profile_prompt_block（主链同款）。
所有函数动态读取、禁止模块级缓存（角色切换即时生效 + 用户隔离铁律）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("proactive_context")

# 完整版人设总长上限：覆盖 description 全量 + 回复示例 + 禁令段；
# 超长截断（旧版 400+200 裁掉【回复示例】【绝对禁止】是生产实证的口吻漂移根因）
_PERSONA_HINT_MAX_CHARS = 1200
# 卡片分支各段截断（mes_example / creator_notes）
_MES_EXAMPLE_MAX_CHARS = 600
_CREATOR_NOTES_MAX_CHARS = 400


def load_full_persona_hint(character_id: str) -> str:
    """完整版人设（替代旧 ``llm_proactive.load_persona_hint`` 的 400+200 裁剪版）。

    - ``default``/``demo`` → ``config/persona.yaml``：``description`` 全量
      （YAML 多行块，自带【核心性格】【说话风格】【回复示例】【绝对禁止】四段）
      + ``creator_notes`` 的「## 通用禁令」小节（到 creator_notes 结尾）。
      「身份拷问应答」段主动场景用不上，跳过。
    - 其他 id → ``config/characters/{cid}.json``（miss 时 glob 按 id/name 兜底）：
      ``description`` + ``personality_text`` + ``speaking_style`` +
      ``mes_example``[:600] + ``creator_notes``[:400]，每段带小标题行。
    - 组装后总长上限 1200 字；取不到任何内容返回 ""。

    每次调用动态读盘（无模块级缓存）：切角色即时生效 + 用户隔离铁律。
    """
    cid = str(character_id or "").strip()
    if not cid:
        return ""
    try:
        from utils.project_paths import project_path

        if cid in ("default", "demo"):
            return _persona_from_yaml(project_path("config", "persona.yaml"))
        return _persona_from_card(project_path("config", "characters"), cid)
    except Exception as e:  # noqa: BLE001
        logger.debug("load_full_persona_hint failed cid=%s: %s", cid, e)
        return ""


def _persona_from_yaml(path) -> str:
    """内置十四（persona.yaml）：description 全量 + 通用禁令小节。"""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return ""
    import yaml

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return ""
    name = str(data.get("name") or "").strip() or "十四"
    desc = str(data.get("description") or "").strip()
    notes = str(data.get("creator_notes") or "")
    segments = [f"角色：{name}"]
    if desc:
        segments.append(desc)
    # 「## 通用禁令」小节：字符串定位到 creator_notes 结尾；身份拷问应答段
    # 只服务主链被直球拷问的场景，主动消息用不上（跳过它前面的全部内容）
    idx = notes.find("## 通用禁令")
    if idx >= 0:
        bans = notes[idx:].strip()
        segments.append(bans)
    hint = "\n\n".join(s for s in segments if s).strip()
    return hint[:_PERSONA_HINT_MAX_CHARS]


def _persona_from_card(root, cid: str) -> str:
    """角色卡分支：config/characters/{cid}.json（glob 按 id/name 兜底匹配）。"""
    import json
    from pathlib import Path

    p = Path(root) / f"{cid}.json"
    if not p.exists() and Path(root).exists():
        for f in sorted(Path(root).glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if str(d.get("id") or "") == cid or str(d.get("name") or "") == cid:
                    p = f
                    break
            except Exception:  # noqa: BLE001
                continue
    if not p.exists():
        return ""
    d = json.loads(p.read_text(encoding="utf-8"))
    name = str(d.get("name") or cid)
    segments: list[str] = [f"角色：{name}"]
    desc = str(d.get("description") or "").strip()
    if desc:
        segments.append(f"【角色设定】\n{desc}")
    personality = str(d.get("personality_text") or "").strip()
    if personality:
        segments.append(f"【性格】\n{personality}")
    style = str(d.get("speaking_style") or "").strip()
    if style:
        segments.append(f"【说话风格】\n{style}")
    mes = str(d.get("mes_example") or "").strip()
    if mes:
        segments.append(f"【回复示例】\n{mes[:_MES_EXAMPLE_MAX_CHARS]}")
    notes = str(d.get("creator_notes") or "").strip()
    if notes:
        segments.append(f"【扮演规则】\n{notes[:_CREATOR_NOTES_MAX_CHARS]}")
    hint = "\n\n".join(s for s in segments if s).strip()
    return hint[:_PERSONA_HINT_MAX_CHARS]


def immersive_proactive_constraints() -> str:
    """按当前回复模式渲染主动消息约束段（决策/生成两线共用）。

    主链 ``utils.reply_mode`` 已有沉浸式/小说式双模式指令，但主动两条线从未
    注入——主动消息游离在模式约束之外（生产实证：沉浸式模式下主动消息仍带
    括号动作、客服腔追问）。本函数按当前模式动态渲染浓缩版（每次调用读真源，
    不缓存）。
    """
    try:
        from utils.reply_mode import REPLY_MODE_NOVEL, read_reply_mode

        if read_reply_mode() == REPLY_MODE_NOVEL:
            return _NOVEL_CONSTRAINTS
        return _IMMERSIVE_CONSTRAINTS
    except Exception as e:  # noqa: BLE001
        logger.debug("immersive_proactive_constraints failed: %s", e)
        return ""


_IMMERSIVE_CONSTRAINTS = """【主动消息模式约束（沉浸式真人聊天）】
- 只发一条微信里真的会打出来的那句话，10~60 字；严禁括号动作/神态/旁白描写，
  严禁星号叙述。
- 非共处：你和对方不在同一物理空间，只能靠手机文字交流；禁止「我去占位置等你」
  「我们面对面聊」「咖啡馆见」这类需要身体在场的共处话术。
- 禁客服腔：不得连抛疑问句轰炸（「有什么心事吗？需要我陪你聊聊吗？」式）；
  不得模板化安慰（「我在这里陪着你」）；不得索取式收尾（「分享一下吧」）。
- 话锋对齐：必须接得上最近对话的话题或画像里的真实近况；很久前的话题不得
  当作「刚刚」提起，不得凭空开新话头。"""

_NOVEL_CONSTRAINTS = """【主动消息模式约束（小说式）】
- 允许用括号写动作、神态细节营造画面感，但一条就好，对话本身仍要自然。
- 仍禁客服腔：不得连续疑问句轰炸（「有什么心事吗？需要我陪你聊聊吗？」式）；
  不得模板化安慰（「我在这里陪着你」）；不得索取式收尾（「分享一下吧」）；
  不得编造对方没说过的处境。
- 话锋对齐：必须接得上最近对话的话题或画像里的真实近况；很久前的话题不得
  当作「刚刚」提起，不得凭空开新话头。"""
