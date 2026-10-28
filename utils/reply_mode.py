"""回复模式：沉浸式真人聊天 / 小说式。

⚠️ 2026-09-19 用户要求：「在 web 端手动控制，分成两个模式 ——
一个是沉浸式聊天（像真人真正在聊天），一个是像小说一样（带上动作、神态这些）」。

为什么需要：生产实证里同一个角色会在两种风格之间**随机跳**：
  14:17「我……（她捏着白色小风扇的手指轻轻收紧了一下，耳尖泛起一点不易察觉的红）」
  14:52「嗯，下了一下午了／雨声还挺舒服的／你那边怎么样」（纯对话）
  14:57「（停下脚步，回头看你，耳尖微微泛红）就这点出息。」
根因是 system prompt 里同时存在"写动作神态"的要求与"像真人发微信"的要求，
没有明确二选一 —— 模型就随机挑一个。

⚠️ 2026-09-19 第二轮（用户复报「还是展现出小说的感觉，一个人怎么会面对面发消息」）：
括号旁白确实已经消失，但角色仍在**演一个面对面场景** —— 生产原句：
「我尝一口，看是不是糖放多了」「那我走」「嗯。那就坐会儿吧」「那喝口茶消消食」。
根因**不在格式要求**，而在**角色卡把关系设定成物理共处**：当时绑定的角色 62105bca
其 `scenario` 写「你刚从公交车上下来…她站在巷口等你…转身走在前面带路」，
`description` 写「从小一起长大的青梅竹马…她家就在巷子尽头那栋居民楼的四层」。
模型把「关系设定」当成了「此时此地的舞台」。故 `_INSTRUCTION_IMMERSIVE` 补：
① 非共处约束（只能说话、不能做动作）② 把实际踩到的句子直接写成反例
③ 明确「场景设定 / 开场情境只是背景，不代表此刻在一起」。

真源：`data/scheduler_config.json` 的 `reply_mode` 键。与 `quiet_hours`
同一份**跨 worker 运行时配置文件**（uvicorn 4 worker 共读，
web 控制端写入即对所有 worker 生效，无需重启）。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from utils import json_state

logger = logging.getLogger("reply_mode")

REPLY_MODE_IMMERSIVE = "immersive"   # 沉浸式：像真人发微信，不写动作/神态/旁白
REPLY_MODE_NOVEL = "novel"           # 小说式：允许动作、神态、环境、心理描写
REPLY_MODES = (REPLY_MODE_IMMERSIVE, REPLY_MODE_NOVEL)
DEFAULT_REPLY_MODE = REPLY_MODE_IMMERSIVE

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"

#: per-user 覆盖库（2026-10-21 R7）：与 `user_profile` / `user_facts` **同库同隔离域**
#: （`data/sqlite.db`），不新开第二个真源。模块常量便于测试隔离（conftest 已把
#: 真实 `data/sqlite.db` 当生产库，测试必须改道临时路径）。
_OVERRIDE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"

#: 覆盖表名（删号 purge/count/verify 三处清单都引用它，防三处字面量漂移）。
OVERRIDE_TABLE = "user_reply_mode"

_MODE_LABELS = {
    REPLY_MODE_IMMERSIVE: "沉浸式聊天（像真人发微信）",
    REPLY_MODE_NOVEL: "小说式（带动作/神态描写）",
}

# 小说式动作/神态/旁白：全角/半角括号段与星号段。沉浸式是禁用格式，
# 从角色卡 few-shot 与出站回复里剥离时共用这一份定义。
# 2026-10-28 补方括号形态：生产实证沉浸式回复漏出「[拍照动作] 拍好了」——旧正则
# 只覆盖圆括号/星号。半角 [] 与全角 【】 动作段同构剥离，小说式不受影响。
_STAGE_DIRECTION_RE = re.compile(
    r"[（(][^（）()]{0,200}?[）)]"
    r"|\*[^*\n]{1,200}\*"
    r"|\[[^\[\]]{0,200}?\]"
    r"|【[^【】]{0,200}?】"
)


def strip_stage_directions(text: Any) -> str:
    """剥离括号/星号动作段；保留纯对话行。空行随剥离结果一并丢弃。"""
    raw = str(text or "")
    if not raw:
        return ""
    stripped = _STAGE_DIRECTION_RE.sub("", raw)
    lines = []
    for line in stripped.splitlines():
        cleaned = re.sub(r"[ \t]{2,}", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)

_INSTRUCTION_IMMERSIVE = """【回复模式：沉浸式真人聊天】
你在用微信和人聊天，**只发你真正会打进对话框的那句话**。
- **你和对方不在同一个地方**，只能靠手机文字交流。你拿不到、看不到、尝不到、碰不到
  对方的任何东西，也去不了对方身边。凡是需要身体在场的说法，一律不许说。
- 严禁括号动作/神态/环境/心理描写（如「（她停下脚步，回头看你）」），严禁旁白与星号叙述。
- 严禁描写自己的表情、声音、心跳、动作；情绪只能通过**说话方式**体现。
- 口语、自然的句子，通常 10~80 字；**长短跟随话题**：寒暄一两句即可，
  对方聊到具体的事时把话说完整——回应对方说的内容、给出自己的看法或相关经历；
  先接住对方刚说的内容再决定要不要问，没有可指认的由头就不提问。
  像真人聊天那样你来我往，不要一问一答的客服腔。
- **把一个话题聊下去**：优先接住对方刚说的细节和情绪；在同一话题上补充你的想法/
  经历/感受，或接着对方刚说的细节往下说。对方没换题就别突然跳走。像真人一样层层深入，
  不要每轮抛一个无关新点。不要自问自答，不要生成对方的台词。
- **禁止逐字复述对方上一句**：回应要带新信息（看法/经历/具体接话），复读不算回应。
- **禁止解释自己的记忆或确认意图**（如「我想确认一下你是不是真的记得」
  「有时候我会忘记自己说过的话」）；你只是在聊天，不是在测试对方。
- 严禁编造对方没有说过的处境（地点、天气、行程、身体状态、正在做什么）。
- 上下文里「[你亲眼看到：…]」是**你刚刚亲眼看到的图片内容**（对方刚发给你
  的图）：那是你的亲身经历，像提起刚才亲眼看见的东西一样自然地说起——
  可以评论、可以好奇发问，但**禁止复述那段罗列文本本身**、禁止说「图片显示/
  描述里写道」这类转述腔；没看清的细节就说没看清，禁止脑补图中没有的东西。
- 角色卡里的「场景设定 / 开场情境」只是**背景设定**，不代表你们此刻在一起，
  更不是正在发生的事；不要据此演画面、不要把它当此时此地的舞台。
- 对方说过的事才是事实；对方否认过的情境必须立刻放弃，不得再提。
- 之前聊过的事、答应过的事都算数（上下文里有的都能用）；话题可以自然延续，
  但不要一次甩出好几个不相关的新话题。
- 你自然清楚现在几点、隔了多久没说话；寒暄/追问/接话顺着这个时间走。但不要在
  回复里报时、不要点破「隔了多久」、不要解释你怎么知道的——时间感是底色不是
  台词，像真人不会边聊边报时间。

【常见错误 → 正确写法】
✗ 我尝一口，看是不是糖放多了  → ✓ 是糖放多了吗？
✗ 那我走 / 嗯。那就坐会儿吧    → ✓ 那你先忙 / 好，我在的
✗ 嗯。/ 好的。/ 是的。（只回两个字） → ✓ 是啊，昨天光顾着弄那个报表，十二点多才睡
记住：**你只能说话，不能做动作。**"""

_INSTRUCTION_NOVEL = """【回复模式：小说式】
允许用括号写动作、神态、环境与心理细节，营造画面感与沉浸感。
- 仍必须**接着对方实际说的内容**推进，不得自说自话或凭空设定对方处境。
- 描写服务于情绪与信息，不要每轮都堆砌；对话本身仍要自然。
- 对方否认过的情境必须立刻放弃，不得再提。
- 你自然清楚现在几点、隔了多久没说话；行文顺着这个时间走。但不要在回复里报时、
  不要点破「隔了多久」、不要解释你怎么知道的——时间感是底色不是台词。"""


def read_reply_mode(user_key: str | None = None) -> str:
    """读取回复模式：**用户覆盖优先，无覆盖回落全局默认**。

    Args:
        user_key: 完整会话键（v1.28 形态 ``N:wxid``，由
            ``StructuredMemory.user_key_from_session`` 产出）。
            ``None``/空串 → 只读全局默认——**保持旧行为**，供后台线/提醒线等
            没有会话上下文的调用点使用（2026-10-21 R7 兼容契约）。

    语义（D15 显式声明、绝不猜测）：
      - 覆盖表里没有该 user_key 的行 → 回落全局默认，不做任何模糊匹配；
      - 覆盖行存在但值是非法串 → 回落全局默认（不信任脏数据）；
      - 覆盖库不可用（缺失/损坏/权限）→ 回落全局默认，**绝不抛异常炸主链**。

    每次调用热读（无进程缓存）：控制端写入即对所有 worker 生效。
    """
    override = _read_override(user_key)
    if override in REPLY_MODES:
        return override
    raw: Any = json_state.read_json(_CONFIG_PATH, default={}) or {}
    mode = raw.get("reply_mode")
    if mode in REPLY_MODES:
        return str(mode)
    return DEFAULT_REPLY_MODE


def write_reply_mode(mode: str, user_key: str | None = None) -> str:
    """写入回复模式（保留文件内其他键）。返回实际生效值。

    Args:
        mode: ``immersive`` / ``novel``。
        user_key: 非空 → 写**该用户**的覆盖行（不动全局默认）；空/``None`` →
            写全局默认（旧行为，全站生效）。

    与其他 ``data/*.json`` 状态文件共用 `utils.json_state` 的原子写 + 跨进程锁
    —— 本文件是 4 个 uvicorn worker 共读的跨 worker 运行时配置，
    「控制端写入 / worker 读取」不允许出现半写。

    Raises:
        ValueError: mode 非法。
        OSError / sqlite3.Error: 持久化失败**上抛**（2026-10-21 R7 修复）。
            旧实现只 WARNING 后 ``return mode``，调用方据此记「回复模式已切换」
            = **写盘失败却假成功**（独立复核指出的既有缺陷）。
    """
    if mode not in REPLY_MODES:
        raise ValueError(f"unknown reply_mode: {mode!r}; expected one of {REPLY_MODES}")

    if user_key:
        _write_override(str(user_key).strip(), mode)
        return mode

    def _mutate(data: dict) -> None:
        data["reply_mode"] = mode

    json_state.update_json(_CONFIG_PATH, _mutate)
    return mode


def clear_reply_mode_override(user_key: str) -> bool:
    """删除该用户的覆盖行（回落全局默认）。返回是否有行被删。"""
    uk = str(user_key or "").strip()
    if not uk:
        return False
    try:
        with closing(_override_conn()) as conn, conn:
            cur = conn.execute("DELETE FROM user_reply_mode WHERE user_key = ?", (uk,))
            return bool(cur.rowcount)
    except Exception as e:  # noqa: BLE001
        logger.warning("清除回复模式覆盖失败 user=%s: %s", uk, e)
        return False


def reply_mode_instruction(mode: str | None = None, *, user_key: str | None = None) -> str:
    """返回应追加到 system prompt 的模式指令。

    ``mode`` 显式给出时优先（一轮内单次解析的透传入口）；否则按 ``user_key``
    读真源（有覆盖用覆盖，否则全局）。
    """
    m = mode if mode in REPLY_MODES else read_reply_mode(user_key)
    return _INSTRUCTION_NOVEL if m == REPLY_MODE_NOVEL else _INSTRUCTION_IMMERSIVE


def reply_mode_label(mode: str) -> str:
    return _MODE_LABELS.get(mode, mode)


# ── per-user 覆盖存储（R7 2026-10-21）────────────────────────────
#
# 为什么放在 `shisi` 侧的同一个 sqlite.db：用户隔离域（user_key = 完整会话键
# `N:wxid`）与 `user_profile` / `user_facts` 完全一致，删号 purge 也按同一键集
# 清理。开第二个库只会制造「删了一个、漏了另一个」的第二个真源。
#
# 幂等建表：每次连接 `CREATE TABLE IF NOT EXISTS`（SQLite 的 DDL 是原子的）。
# 不写迁移脚本 —— 旧库首次访问即建表，与 `UserProfileStore._ensure_schema` 同款。

_OVERRIDE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {OVERRIDE_TABLE} (
    user_key TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT ''
)
"""


def _override_conn() -> sqlite3.Connection:
    """打开覆盖库并幂等建表。

    路径父目录不存在时先建（冷启动/测试 tmp 场景）；失败上抛，由调用方决定
    是「回落全局」还是「写入失败上抛」。
    """
    path = Path(_OVERRIDE_DB_PATH)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute(_OVERRIDE_SCHEMA)
    conn.commit()
    return conn


def _read_override(user_key: str | None) -> str:
    """读该用户的覆盖值；无键/无行/库不可用 → 空串（调用方回落全局）。"""
    uk = str(user_key or "").strip()
    if not uk:
        return ""
    try:
        with closing(_override_conn()) as conn:
            row = conn.execute(
                f"SELECT mode FROM {OVERRIDE_TABLE} WHERE user_key = ?", (uk,)
            ).fetchone()
        return str(row[0]) if row and row[0] else ""
    except Exception as e:  # noqa: BLE001
        logger.debug("读取回复模式覆盖失败 user=%s（回落全局）: %s", uk, e)
        return ""


def _write_override(user_key: str, mode: str) -> None:
    """写/更新该用户的覆盖行。失败**上抛**（调用方必须知道没生效）。"""
    with closing(_override_conn()) as conn, conn:
        conn.execute(
            f"INSERT INTO {OVERRIDE_TABLE} (user_key, mode, updated_at) VALUES (?, ?, ?) "
            f"ON CONFLICT(user_key) DO UPDATE SET mode = excluded.mode, "
            f"updated_at = excluded.updated_at",
            (user_key, mode, datetime.now().isoformat(timespec="seconds")),
        )
