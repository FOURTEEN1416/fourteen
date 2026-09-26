"""
训练管线 + 主动搭话路由 — /api/training/* + /api/proactive/*

来源：原 api.main_routes.py L365/379/459/512/544/549/590/638/647/664/1252 共 11 端点
（2026-08-28：/api/training/extract 已移除——微信克隆收敛为"本地工具提取 + 上传 JSON"，
服务端不做任何微信数据提取，见 /api/clone/upload）

注意：LoRA 微调训练端点已移除（项目使用外接 API + RAG + 提示词注入）。
保留：数据清洗 / 测试 / 应用（克隆到 ToneMimic）+ 主动搭话配置。

依赖：
- deps.orch（_ase 主动搭话引擎）
- deps.training_mgr（训练状态管理器）
- 训练管线: clone_training.data_cleaner / my_character.tone_mimic
- ProactiveConfigRequest 模型来自 api.main_routes
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from pydantic import BaseModel, Field

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps
from api.main_routes import ProactiveConfigRequest

logger = logging.getLogger("api.routers.training_routes")

router = APIRouter(tags=["training"])


# ═══════════════════════════════════════════════════════
# Training Pipeline Status
# ═══════════════════════════════════════════════════════


@router.get("/api/training/status")
async def training_status(_auth: bool = Security(verify_api_key_dep)):
    try:
        import clone_training  # noqa: F401
        available = True
        desc = "风格克隆管线已就绪（提示词注入模式，无 LoRA 训练）"
    except ImportError:
        available = False
        desc = "训练模块未安装"
    return {"available": available, "description": desc, "steps": [
        "style_analyze", "tone_mimic_inject",
    ]}


@router.get("/api/training/progress")
async def get_training_progress(_auth: bool = Security(verify_api_key_dep)):
    return deps.training_mgr.get_state()


# ═══════════════════════════════════════════════════════
# Training Pipeline (admin only)
# ═══════════════════════════════════════════════════════


@router.post("/api/training/clean")
async def start_cleaning(
    accept_score: int = 2,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    def _do_clean():
        try:
            from clone_training.data_cleaner import DataCleaner
            from llm_provider import get_llm
            llm = get_llm()
            cleaner = DataCleaner(llm=llm, accept_score=accept_score)
            data_dir = _training_dir()
            json_files = sorted(data_dir.glob("*.jsonl"))
            if not json_files:
                raise FileNotFoundError("No dataset found")
            latest = str(json_files[-1])
            result_path = cleaner.score_from_dataset(latest)
            cleaned_count = 0
            if result_path:
                try:
                    with open(result_path, encoding="utf-8") as f:
                        cleaned_data = json.load(f)
                    cleaned_count = len(cleaned_data) if isinstance(cleaned_data, list) else 0
                except Exception as e:
                    logger.debug("Failed to read cleaned data result: %s", e)
            deps.training_mgr.update(
                status="cleaned",
                cleaned_turns=cleaned_count,
                progress=0.6,
                step_name="数据清洗",
            )
        except Exception:
            logger.exception("Cleaning failed")
            deps.training_mgr.update(status="error", error="internal_error")

    deps.training_mgr.update(status="cleaning", start_time=time.time(), step_name="数据清洗")
    deps.training_mgr.submit(_do_clean)
    return {"status": "started", "task": "clean", "accept_score": accept_score}


def _live_tone_mimic():
    """优先用编排器在跑的 ToneMimic（与 RAG 检索同一实例）；无则按需构建。"""
    orch = deps.orch
    tone = orch.components.get("tone") if orch and orch.components else None
    if tone is not None:
        return tone
    from my_character.tone_mimic import ToneMimic

    chroma_path = str(Path(__file__).parent.parent.parent / "data" / "chroma_db")
    return ToneMimic(chroma_path=chroma_path)


def _style_preview_sync(message: str) -> dict:
    """同步段：ToneMimic 构造（Chroma/ONNX 首次加载可达数秒）+ 风格画像读取。"""
    mimic = _live_tone_mimic()
    style_prompt = mimic.get_style_prompt()
    examples = mimic.retrieve_style_examples(message, top_k=3)
    return {"message": message, "style_output": style_prompt,
            "style_examples": examples, "status": "ok"}


@router.post("/api/training/test")
async def test_clone(
    message: str = Query(..., max_length=1000),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    try:
        # 构造/检索是同步阻塞 IO，必须在 worker 线程跑，否则卡住整个事件循环
        return await asyncio.to_thread(_style_preview_sync, message)
    except (ImportError, OSError, ValueError):
        logger.exception("Test clone failed")
        return {"message": message, "style_output": "", "status": "error", "detail": "internal_error"}


def _training_dir() -> Path:
    return Path(__file__).parent.parent.parent / "data" / "training"


def _apply_cleaned_sync() -> dict:
    """把最近一轮清洗产物（*_cleaned.json）真实灌入在跑的 ToneMimic 风格库。"""
    data_dir = _training_dir()
    cleaned_files = sorted(data_dir.glob("*_cleaned.json"))
    if not cleaned_files:
        raise FileNotFoundError("No cleaned dataset found; run /api/training/clean first")
    latest = cleaned_files[-1]
    with open(latest, encoding="utf-8") as f:
        cleaned = json.load(f)
    if not isinstance(cleaned, list):
        raise ValueError(f"清洗结果格式异常: {type(cleaned).__name__}")
    mimic = _live_tone_mimic()
    added = 0
    for conv in cleaned:
        if not isinstance(conv, dict):
            continue
        user_msg = (conv.get("user_msg") or conv.get("user") or "").strip()
        reply_msg = (conv.get("reply_msg") or conv.get("reply") or "").strip()
        if not user_msg or not reply_msg:
            continue
        mimic.add_conversation(
            user_msg, reply_msg,
            metadata={"source": "training_apply", "file": latest.name},
        )
        added += 1
    return {"status": "applied", "path": str(latest), "applied": added}


@router.post("/api/training/apply")
async def apply_clone(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    # 旧实现只做 Path 拼接就返回 "applied"——从未写入任何风格库（假成功）。
    try:
        result = await asyncio.to_thread(_apply_cleaned_sync)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except (ValueError, OSError):
        logger.exception("Apply clone failed")
        raise HTTPException(status_code=500, detail="internal_error") from None
    deps.training_mgr.update(status="applied", step_name="克隆应用", progress=1.0)
    return result


# ═══════════════════════════════════════════════════════
# Proactive Engine
# ═══════════════════════════════════════════════════════


@router.get("/api/proactive/state")
async def proactive_state(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._ase:
        state = orch._ase.health_check()
        # 块4：读数与真源同源 —— 引擎内存里那份只反映本 worker，暂停由
        # 任一 worker 发起时本端必须仍显示真实状态。
        from proactive.scheduler import ProactiveScheduler

        blk = (ProactiveScheduler._read_config_file() or {}).get("proactive") or {}
        state["paused"] = bool(blk.get("paused", getattr(orch._ase, "_paused", False)))
        return state
    return {}


def _scheduler_or_none():
    return deps.orch.components.get("scheduler") if deps.orch else None


def _plane():
    """控制面 ``proactive.runtime_plane``；不可用时 None（退回本进程内存历史）。"""
    try:
        from proactive import runtime_plane

        return runtime_plane
    except Exception as e:  # noqa: BLE001
        logger.debug("控制面不可用: %s", e)
        return None


def _file_quiet_hours() -> tuple[int, int]:
    """无调度器的 worker（4 worker 部署仅 master 持有）从配置文件读免打扰。"""
    from proactive.scheduler import ProactiveScheduler

    qh = (ProactiveScheduler._read_config_file() or {}).get("quiet_hours") or {}
    try:
        return (int(qh.get("start", 23)), int(qh.get("end", 7)))
    except (TypeError, ValueError):
        return (23, 7)


def _file_vault_config() -> dict:
    from proactive.scheduler import ProactiveScheduler

    vault = (ProactiveScheduler._read_config_file() or {}).get("vault") or {}
    return {
        "enabled": bool(vault.get("enabled", False)),
        "interval_minutes": int(vault.get("interval_minutes", 60)),
    }


@router.get("/api/proactive/config")
async def get_proactive_config(_auth: bool = Security(verify_api_key_dep)):
    """运行时参数真值（阈值/频率控制器对象，非展示字典）。"""
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")
    config = orch._ase.get_runtime_config()
    scheduler = _scheduler_or_none()
    if scheduler is not None and hasattr(scheduler, "get_quiet_hours"):
        start, end = scheduler.get_quiet_hours()
    else:
        start, end = _file_quiet_hours()
    config["quiet_hours_start"] = start
    config["quiet_hours_end"] = end
    # 对话内追问参数（web 控制端可调；真源 data/scheduler_config.json 的 follow_up 块）
    from wechat_direct.wechat_connector import read_follow_up_config

    config["follow_up"] = read_follow_up_config()
    # 回复模式（沉浸式真人 / 小说式）：跨 worker 真源，编排器每次组装提示词时读取
    from utils.reply_mode import read_reply_mode, reply_mode_label

    _mode = read_reply_mode()
    config["reply_mode"] = _mode
    config["reply_mode_label"] = reply_mode_label(_mode)
    # LLM 主动决策 web 参数（2026-09-21）
    scheduler2 = _scheduler_or_none()
    if scheduler2 is not None and hasattr(scheduler2, "get_llm_proactive_config"):
        config["llm_proactive"] = scheduler2.get_llm_proactive_config()
    else:
        from proactive.llm_proactive import read_web_proactive_config

        config["llm_proactive"] = read_web_proactive_config()
    return config


@router.post("/api/proactive/config")
async def update_proactive_config(
    req: ProactiveConfigRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """更新主动消息运行时参数——2026-08-28 修复：旧版只写展示字典不生效。"""
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")
    ase = orch._ase
    ase.apply_runtime_config(
        threshold=req.threshold,
        max_daily_messages=req.max_daily,
        min_interval_minutes=req.min_interval_minutes,
        cooldown_after_reply_minutes=req.cooldown_after_reply_minutes,
    )
    # 免打扰时段（09-17 web 可调）：live scheduler 立即生效；
    # 无论本 worker 是否 master 都写文件（master 下个 tick 重载）
    if req.quiet_hours_start is not None or req.quiet_hours_end is not None:
        from proactive.scheduler import ProactiveScheduler

        scheduler = _scheduler_or_none()
        if scheduler is not None and hasattr(scheduler, "get_quiet_hours"):
            cur_start, cur_end = scheduler.get_quiet_hours()
        else:
            cur_start, cur_end = _file_quiet_hours()
        new_start = req.quiet_hours_start if req.quiet_hours_start is not None else cur_start
        new_end = req.quiet_hours_end if req.quiet_hours_end is not None else cur_end
        if scheduler is not None and hasattr(scheduler, "set_quiet_hours"):
            scheduler.set_quiet_hours(new_start, new_end)
        else:
            ProactiveScheduler.write_config_file(quiet_hours=(new_start, new_end))

    # 对话内追问参数（web 可调，2026-09-19）：连接器每次操作都读该文件，
    # 因此写入即对所有 worker 生效，无需广播/重启。
    if any(
        v is not None
        for v in (
            req.follow_up_enabled,
            req.follow_up_delay1_seconds,
            req.follow_up_delay2_seconds,
            req.follow_up_daily_max,
        )
    ):
        from proactive.scheduler import ProactiveScheduler
        from wechat_direct.wechat_connector import read_follow_up_config

        fu = read_follow_up_config()
        if req.follow_up_enabled is not None:
            fu["enabled"] = bool(req.follow_up_enabled)
        if req.follow_up_delay1_seconds is not None:
            fu["delay1_seconds"] = int(req.follow_up_delay1_seconds)
        if req.follow_up_delay2_seconds is not None:
            fu["delay2_seconds"] = int(req.follow_up_delay2_seconds)
        if req.follow_up_daily_max is not None:
            fu["daily_max"] = int(req.follow_up_daily_max)
        ProactiveScheduler.write_config_file(follow_up=fu)
        logger.info("对话内追问配置已更新: %s", fu)

    # 回复模式（沉浸式真人 / 小说式）：编排器每次组装提示词时读该文件，
    # 写入即对所有 worker 生效，无需重启。
    if req.reply_mode is not None:
        from utils.reply_mode import write_reply_mode

        write_reply_mode(req.reply_mode)
        logger.info("回复模式已切换: %s", req.reply_mode)

    # LLM 主动决策参数（web 可调；注入 prompt，非硬编码日程表）
    if any(
        v is not None
        for v in (
            req.llm_proactive_enabled,
            req.llm_proactive_style_hint,
            req.llm_proactive_intensity,
            req.llm_proactive_respect_quiet,
            req.llm_proactive_character_hint,
        )
    ):
        from proactive.scheduler import ProactiveScheduler

        payload = {
            "enabled": req.llm_proactive_enabled,
            "style_hint": req.llm_proactive_style_hint,
            "intensity": req.llm_proactive_intensity,
            "respect_quiet_hours": req.llm_proactive_respect_quiet,
            "character_hint": req.llm_proactive_character_hint,
        }
        ProactiveScheduler.write_config_file(
            llm_proactive={k: v for k, v in payload.items() if v is not None}
        )
        logger.info("LLM 主动决策配置已更新: %s", {k: v for k, v in payload.items() if v is not None})

    logger.info(
        "Proactive config updated: threshold=%s max_daily=%s",
        ase._urgency_threshold, ase.get_runtime_config()["max_daily_messages"],
    )
    return {"status": "ok", "config": await get_proactive_config(_auth=True)}


class KnowledgeCollectConfigRequest(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=10, le=1440)


@router.get("/api/knowledge/collect-config")
async def get_knowledge_collect_config(_auth: bool = Security(verify_api_key_dep)):
    """知识库定期采集（Vault collect）开关状态（live 优先，文件兜底）。"""
    scheduler = _scheduler_or_none()
    if scheduler is not None and hasattr(scheduler, "get_vault_config"):
        return {**scheduler.get_vault_config(), "available": True}
    return {**_file_vault_config(), "available": True}


@router.post("/api/knowledge/collect-config")
async def update_knowledge_collect_config(
    req: KnowledgeCollectConfigRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """web 控制端开关：启用/停用知识库定期采集（立即生效并持久化）。"""
    scheduler = _scheduler_or_none()
    if scheduler is not None and hasattr(scheduler, "set_vault_collect"):
        result = scheduler.set_vault_collect(
            enabled=bool(req.enabled),
            interval_minutes=req.interval_minutes,
        )
    else:
        # 非 master worker：写文件，master 下个 ASE tick（≤5 分钟）重载生效
        from proactive.scheduler import ProactiveScheduler

        if req.enabled is None:
            current = _file_vault_config()
            req.enabled = current["enabled"]
        ProactiveScheduler.write_config_file(
            vault_enabled=bool(req.enabled),
            vault_interval=req.interval_minutes,
        )
        result = _file_vault_config()
    return {"status": "ok", "config": result}


class ProactivePauseRequest(BaseModel):
    paused: bool


@router.post("/api/proactive/pause")
async def pause_proactive(
    req: ProactivePauseRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """暂停/恢复主动消息调度（暂停后**生成前**即短路，不影响手动发送）。

    块4（缺陷 D）：写 `data/scheduler_config.json` 为跨 worker 真源 —— 旧实现
    只 `orch._ase.apply_runtime_config(paused=...)`（本 worker 内存），而生产
    4 worker 中只有 master 跑调度器：POST 落到别的 worker 时暂停完全无效，
    重启亦归零。
    """
    from proactive.scheduler import ProactiveScheduler

    scheduler = _scheduler_or_none()
    if scheduler is not None and hasattr(scheduler, "set_paused"):
        paused = bool(scheduler.set_paused(req.paused))
    else:
        # 非 master worker：只写文件，master 下个 tick（≤5 分钟）重载生效；
        # 本 worker 若持有引擎也同步一份，保持 /state 读数一致。
        ProactiveScheduler.write_config_file(proactive_paused=bool(req.paused))
        paused = bool(req.paused)
    orch = deps.orch
    if orch and orch._ase and hasattr(orch._ase, "apply_runtime_config"):
        orch._ase.apply_runtime_config(paused=paused)
    logger.info("Proactive scheduler paused=%s", paused)
    return {"status": "ok", "paused": paused}


class ProactiveSendRequest(BaseModel):
    message_type: str | None = None  # 缺省按紧迫度自动选择
    session_key: str | None = None  # 显式授权目标会话（多会话时必填）


# 非 master worker 转投控制命令后等待真实回执的上限（master 消费间隔 5s +
# 生成/投递耗时；``CLAIM_TTL`` 远大于此值，超时不会把命令吞掉）。
_MANUAL_SEND_WAIT_SECONDS = 45.0


def _resolve_manual_target(hub, explicit: str | None) -> str:
    """手动发送的目标会话（W3 缺陷 E：绝不再取 hub「最近一个」引擎）。

    旧实现 ``resolve_engine_for_manual_send()`` 返回的是**最后被触碰的**引擎，
    与控制台想发给谁毫无关系，再叠加 ``_send_to_all`` 广播 —— 给 A 的自测消息
    会打给所有绑定用户。现规则：显式指定优先；仅一路会话时可省略；多路会话
    必须显式指定（400），宁可让调用方补参数也不猜。
    """
    key = str(explicit or "").strip()
    if key:
        return key
    keys = [str(k) for k in (getattr(hub, "known_user_keys", lambda: [])() or []) if str(k).strip()]
    if len(keys) == 1:
        return keys[0]
    if not keys:
        raise HTTPException(503, "无可用的主动消息会话（暂无用户绑定）")
    raise HTTPException(
        400,
        f"存在 {len(keys)} 路会话，手动发送必须显式指定 session_key（避免发给他人）",
    )


@router.post("/api/proactive/send")
async def send_proactive_now(
    req: ProactiveSendRequest | None = None,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """手动在**指定会话**上生成并投递一条主动消息（自测入口）。

    三条纪律（旧实现三条都反着做）：

    1. **目标显式** —— 只投 ``session_key`` 那一路会话，不广播、不挑最近引擎。
    2. **复用真实投递** —— 生成与投递都在 ``ProactiveScheduler.manual_send_now``
       里走 ``_deliver``，其返回值才是 ``delivered``；静默时段在生成**前**拒绝，
       通道拒收不报已受理（旧实现 ``create_task`` 一建立即 ``delivered=True``）。
    3. **不预记账** —— ``sent_history`` 只在受理后写；当日配额在出口归还
       （2026-09-18 生产实证：自测几次耗尽当日 8 条额度使自动消息全天停发）。

    生产 4 worker 中 3 个没有调度器（``run_api._ensure_scheduler_singleton`` 选主
    后把非 master 的 ``components['scheduler']`` 置 None），故本端点不假设本机
    有通道：无调度器时落**持久控制命令**并等待 master 回写的真实回执。
    """
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")

    hub = orch._ase
    target = _resolve_manual_target(hub, req.session_key if req else None)
    msg_type = req.message_type if req else None

    scheduler = orch.components.get("scheduler") if orch.components else None
    if scheduler is not None and hasattr(scheduler, "manual_send_now"):
        result = await asyncio.to_thread(scheduler.manual_send_now, target, msg_type)
        logger.info(
            "Proactive manual send(本机): session=%s delivered=%s",
            target, result.get("delivered"),
        )
        return result

    rp = _plane()
    if rp is None:
        raise HTTPException(503, "本 worker 无调度器且控制面不可用，无法投递主动消息")
    cmd_id = rp.enqueue_control(
        "proactive_manual", target,
        json.dumps({"message_type": msg_type}, ensure_ascii=False),
    )
    receipt = await asyncio.to_thread(
        rp.wait_for_control, cmd_id, _MANUAL_SEND_WAIT_SECONDS
    )
    # SQLite 行内 result/fail_reason 为 NOT NULL DEFAULT ''，但测试桩/旧库可能缺键
    raw = str((receipt or {}).get("result") or "")
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        payload = {}
    if not isinstance(payload, dict) or not payload:
        status = str((receipt or {}).get("status") or "no_receipt")
        payload = {
            "status": "pending" if status == "pending" else "not_delivered",
            "delivered": False,
            "reason": str((receipt or {}).get("fail_reason") or f"master_{status}"),
        }
    payload.setdefault("session_key", target)
    payload.setdefault("delivered", False)
    logger.info(
        "Proactive manual send(转投 master): session=%s cmd=%s delivered=%s",
        target, cmd_id, payload.get("delivered"),
    )
    return payload


@router.get("/api/proactive/history")
async def proactive_history(
    limit: int = Query(default=50, le=200),
    _auth: bool = Security(verify_api_key_dep),
):
    """发送历史真源 = 控制面出站事实（跨 worker / 跨重启）。

    旧实现读 ``orch._ase.sent_history``：那只是**本 worker 内存**里最后 touched
    引擎的代理列表 —— 4 worker 下各说各话、重启归零，且自动消息与手动消息
    不同源，统计与真实触达脱钩（W3 缺陷 E「统计走旁路」）。
    """
    rp = _plane()
    if rp is not None:
        rows = rp.recent_sends("", int(limit))
        history = [
            {
                "id": r.get("id"),
                "kind": r.get("kind"),
                "channel": r.get("channel"),
                "session_key": r.get("session_key"),
                "message": r.get("message"),
                "status": r.get("status"),
                "created_at": r.get("created_at"),
                "fail_reason": r.get("fail_reason"),
            }
            for r in rows
        ]
        return {"history": history, "total": len(history)}
    orch = deps.orch
    if orch and orch._ase:
        history = list(getattr(orch._ase, "sent_history", []) or [])
        return {"history": history[-limit:], "total": len(history)}
    return {"history": [], "total": 0}

