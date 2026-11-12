"""角色卡定位与 persona 渲染唯一真源（B-3b，2026-11-12）。

迁移自两处重复 owner：
- ``proactive/proactive_context.py`` 的完整版人设渲染（yaml 分支 + 角色卡分支）；
- ``shisi/application/persona_service.py`` 的角色卡文件定位与原始 JSON 读取。

行为约束：同一输入必须产出与迁移前**逐字相同**的字符串（常量、上限、段落
顺序、沉浸式剥离顺序全部原样保留）。所有函数每次调用动态读盘/读模式真源，
禁止模块级缓存——切角色/切模式即时生效 + 用户隔离铁律。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("shisi.application.persona_context")

# 完整版人设总长上限：覆盖 description 全量 + 回复示例 + 禁令段；
# 超长截断（旧版 400+200 裁掉【回复示例】【绝对禁止】是生产实证的口吻漂移根因）
_PERSONA_HINT_MAX_CHARS = 1200
# 卡片分支各段截断（mes_example / creator_notes）
_MES_EXAMPLE_MAX_CHARS = 600
_CREATOR_NOTES_MAX_CHARS = 400


def resolve_card_path(
    character_id: str,
    *,
    match_name: bool = False,
    root: Path | None = None,
) -> Path | None:
    """定位角色卡文件：文件名直查 → 遍历 ``*.json`` 匹配内部 id（可选 name）。

    Args:
        character_id: 角色 id（也用作文件名与内部 id/name 的匹配值）。
        match_name: 为 True 时，内部 ``id`` 不匹配则再按 ``name`` 匹配。
            角色卡渲染分支需要该行为（迁移前 ``_persona_from_card`` 即 id/name
            双匹配）；persona_service 的角色 id 解析走 ``match_name=False``。
        root: 卡库目录；缺省 = ``project_path("config", "characters")``。

    Returns:
        命中文件路径；未命中返回 None。
    """
    cid = str(character_id or "")
    if root is None:
        # 函数内导入：保留既有测试对 ``utils.project_paths.project_path`` 的
        # monkeypatch 语义（模块级绑定会让补丁失效）。
        from utils.project_paths import project_path

        base = Path(project_path("config", "characters"))
    else:
        base = Path(root)

    direct_path = base / f"{cid}.json"
    if direct_path.exists():
        return direct_path

    if not base.exists():
        return None

    try:
        candidates = sorted(base.glob("*.json"))
    except OSError:
        return None
    for f in candidates:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("id") or "") == cid:
            return f
        if match_name and str(data.get("name") or "") == cid:
            return f
    return None


def load_raw_card(
    character_id: str,
    *,
    match_name: bool = False,
    root: Path | None = None,
) -> tuple[dict | None, Path | None]:
    """读取角色卡原始 JSON（不展平、不清洗）。

    解析失败/文件不可读/顶层不是对象时返回 ``(None, None)``，**不抛**——
    调用方（persona_service）据此走既有「缺卡返回 None」路径。

    Returns:
        ``(原始卡片字典, 命中文件路径)``；未找到时两者均为 None。
    """
    path = resolve_card_path(character_id, match_name=match_name, root=root)
    if path is None:
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return data, path


def strip_stage_directions_if_immersive(
    text: str, user_key: str | None = None, character_id: str | None = None,
) -> str:
    """沉浸式下剥掉括号/星号动作段；小说式原样返回。

    2026-10-21 FIX-2：角色卡里 mes_example/creator_notes/description 等字段
    普遍带括号动作（生产 42 张卡：37 张 mes_example、23 张 creator_notes），
    原样拼进主动消息 prompt 等于用第一手 few-shot 教模型违反同一 prompt 里的
    ``_IMMERSIVE_CONSTRAINTS``（「严禁括号动作/神态/旁白」）——与首轮 R1 主链
    缺陷同机理（``character_aggregate`` 已修，主动线被漏掉）。

    每次调用动态读模式真源（无模块级缓存）：切角色/切模式即时生效 + 用户隔离
    铁律。**读模式/导入异常时保守返回原文**——宁可多给一段 few-shot，也不可
    因守卫故障把人设整段吞掉。
    """
    raw = str(text or "")
    if not raw:
        return ""
    try:
        from utils.reply_mode import (
            REPLY_MODE_IMMERSIVE,
            read_reply_mode,
            strip_stage_directions,
        )

        # 2026-10-21 R7 / D-C 2026-11-08：主动线也按「会话 × 角色」快照的模式
        # 解析（快照优先，未设置回落全局）。两者任一为空（无会话/角色上下文的
        # 后台调用）→ 回落全局默认，旧行为不变。
        if read_reply_mode(user_key, character_id) != REPLY_MODE_IMMERSIVE:
            return raw
        return strip_stage_directions(raw)
    except Exception as e:  # noqa: BLE001
        logger.debug("_strip_stage_directions_if_immersive failed: %s", e)
        return raw


def render_persona_hint(character_id: str, user_key: str | None = None) -> str:
    """完整版人设（原 ``proactive_context.load_full_persona_hint`` 的渲染段）。

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
            return _persona_from_yaml(project_path("config", "persona.yaml"), user_key, cid)
        return _persona_from_card(project_path("config", "characters"), cid, user_key)
    except Exception as e:  # noqa: BLE001
        logger.debug("render_persona_hint failed cid=%s: %s", cid, e)
        return ""


def _persona_from_yaml(path, user_key: str | None = None, character_id: str = "default") -> str:
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
    # 2026-10-21 FIX-2：沉浸式下先剥括号动作再拼段（剥空则不注入该段）。
    desc = strip_stage_directions_if_immersive(
        str(data.get("description") or "").strip(), user_key, character_id
    )
    notes = str(data.get("creator_notes") or "")
    segments = [f"角色：{name}"]
    if desc:
        segments.append(desc)
    # 「## 通用禁令」小节：字符串定位到 creator_notes 结尾；身份拷问应答段
    # 只服务主链被直球拷问的场景，主动消息用不上（跳过它前面的全部内容）
    idx = notes.find("## 通用禁令")
    if idx >= 0:
        bans = strip_stage_directions_if_immersive(
            notes[idx:].strip(), user_key, character_id
        )
        if bans:
            segments.append(bans)
    hint = "\n\n".join(s for s in segments if s).strip()
    return hint[:_PERSONA_HINT_MAX_CHARS]


def _persona_from_card(root, cid: str, user_key: str | None = None) -> str:
    """角色卡分支：config/characters/{cid}.json（glob 按 id/name 兜底匹配）。"""
    d, _path = load_raw_card(cid, match_name=True, root=Path(root))
    if d is None:
        return ""
    name = str(d.get("name") or cid)
    segments: list[str] = [f"角色：{name}"]
    # 2026-10-21 FIX-2：**示例/规则段**按模式先剥括号动作再拼；剥空的段不注入
    # （不留空标题，与 character_aggregate 同款策略）。剥离先于截断——截断留在
    # 后面的只可能是未闭合括号，不会漏出完整动作段。
    #
    # 🔴 作用面刻意收窄到 mes_example / creator_notes：这两段整体是 few-shot
    # 示例与模型指令（生产实测卡文括号为 `（拉了拉衣领）`「（顿了顿）」式动作），
    # 原样注入等于用 few-shot 教模型违反同一 prompt 的沉浸式禁令（`character_aggregate`
    # 同机理已修，主动线被漏掉）。**description / personality_text / speaking_style
    # 不动**——实测其括号绝大多数是规则限定语而非动作（如李信「（优先级从高到低，
    # 不可越级违背）」、上杉绘梨衣「（凌驾于所有规则之上…）」），剥掉会扣掉角色规则。
    desc = str(d.get("description") or "").strip()
    if desc:
        segments.append(f"【角色设定】\n{desc}")
    personality = str(d.get("personality_text") or "").strip()
    if personality:
        segments.append(f"【性格】\n{personality}")
    style = str(d.get("speaking_style") or "").strip()
    if style:
        segments.append(f"【说话风格】\n{style}")
    mes = strip_stage_directions_if_immersive(
        str(d.get("mes_example") or "").strip(), user_key, cid
    )
    if mes:
        segments.append(f"【回复示例】\n{mes[:_MES_EXAMPLE_MAX_CHARS]}")
    notes = strip_stage_directions_if_immersive(
        str(d.get("creator_notes") or "").strip(), user_key, cid
    )
    if notes:
        segments.append(f"【扮演规则】\n{notes[:_CREATOR_NOTES_MAX_CHARS]}")
    hint = "\n\n".join(s for s in segments if s).strip()
    return hint[:_PERSONA_HINT_MAX_CHARS]
