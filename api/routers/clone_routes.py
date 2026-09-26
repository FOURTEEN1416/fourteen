"""
好友克隆数据管理路由 — /api/clone*

来源：原 api.main_routes.py L848/855/862/885/898/912/924 共 7 端点

依赖：
- deps.get_clone_mgr() — CloneDataManager 单例
- 涉及 CRUD：contacts / datasets / conversations

架构（2026-07-27 重构）：
- wechat-decrypt 必须运行在用户 Windows 电脑（依赖微信进程 + Windows API）
- 服务器仅接受上传的 JSON 聊天数据 → 分析 → 生成人设预览
- 通过 WECHAT_DECRYPT_PATH 环境变量支持本地模式（开发/测试）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Security, UploadFile

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps

logger = logging.getLogger("api.routers.clone_routes")

router = APIRouter(tags=["clone"])


def normalize_clone_conversations(
    data: list[Any],
) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, int]]:
    """标准化克隆数据（W7 方向根治）。

    - **带 ``is_self`` 的原生导出**（wechat-decrypt，方向明确）：``is_self=true``
      （账号主人）→ user 侧；``is_self=false``（被克隆好友）→ reply 侧。
      该分支**只看本条 text/content**，禁止通用 ``item.get("text")`` 把
      自己的话同时灌进 reply（旧缺陷：同一文本双向重复统计）。
    - **简单格式**（无 ``is_self``）：按 user/reply、speaker/content、
      from/to、talker/content 命名兼容——一行即一组上下文配对。
    - 需要上下文时按行内 user/reply 配对；原生格式行是单侧消息，
      下游 StyleAnalyzer 只消费 reply（目标侧）。

    Returns:
        (conversations, parse_errors, warnings, direction_stats)
        direction_stats: {"self": 自己侧条数, "target": 目标侧条数}
    """
    conversations: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    warnings: list[str] = []
    stats = {"self": 0, "target": 0}
    saw_native = False
    saw_simple = False

    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            parse_errors.append(f"第 {idx+1} 条：非对象格式")
            continue
        # 兼容 wechat-decrypt 的 message_type 字段（1=文本）
        if "message_type" in item and item.get("message_type") != 1:
            continue
        # 兼容时间戳格式
        ts = item.get("timestamp") or item.get("ts") or item.get("createTime") or ""
        if isinstance(ts, (int, float)):
            ts = str(int(ts))

        if "is_self" in item:
            # 原生导出：方向按 is_self 判定，仅本条文本进入对应一侧
            saw_native = True
            text = str(item.get("text") or item.get("content") or "").strip()
            if not text:
                continue
            if item.get("is_self"):
                conversations.append({"user": text, "reply": "", "timestamp": ts})
                stats["self"] += 1
            else:
                conversations.append({"user": "", "reply": text, "timestamp": ts})
                stats["target"] += 1
        else:
            # 简单格式（方向由字段名表达：user/对外 ↔ reply/被克隆者）
            saw_simple = True
            user_msg = (
                item.get("user") or item.get("speaker")
                or item.get("from") or item.get("talker") or ""
            )
            reply_msg = item.get("reply") or item.get("content") or item.get("to") or ""
            if user_msg or reply_msg:
                conversations.append({
                    "user": str(user_msg),
                    "reply": str(reply_msg),
                    "timestamp": ts,
                })
                if reply_msg:
                    stats["target"] += 1
                if user_msg:
                    stats["self"] += 1

    if saw_native and saw_simple:
        warnings.append(
            "数据混有带 is_self 的原生导出与 user/reply 简单格式：原生条目已按 "
            "is_self 定向（false=被克隆者），简单条目按 user/reply 处理。"
        )
    return conversations, parse_errors, warnings, stats


def _build_clone_preview_from_conversations(
    target: str,
    conversations: list[dict[str, Any]],
) -> dict[str, Any]:
    """从已提取的对话列表生成人设预览（核心分析逻辑，与数据来源解耦）。"""
    from clone_training.style_analyzer import StyleAnalyzer

    if not conversations:
        raise ValueError("未找到可用于分析的文本对话")

    profile = StyleAnalyzer().analyze(conversations)
    catchphrases = [phrase for phrase, _count in profile.catchphrases[:8]]
    anchors = [*catchphrases[:3], *profile.slang_examples[:3]]
    dominant_emotion = max(profile.emotion_dist, key=lambda emotion: profile.emotion_dist[emotion]) if profile.emotion_dist else "自然"
    anchors.append(f"{dominant_emotion}表达")
    anchors = list(dict.fromkeys(anchors))[:8]

    positive = profile.emotion_dist.get("正面", 0.0)
    negative = profile.emotion_dist.get("负面", 0.0)
    question_ratio = profile.sentence_type_dist.get("提问", 0.0)
    speaking_style = {
        "formality": 0.35,
        "expressiveness": min(1.0, 0.35 + profile.emoji_freq + profile.kaomoji_freq),
        "humor": min(1.0, 0.3 + profile.slang_freq),
        "directness": max(0.1, min(1.0, 0.75 - question_ratio * 0.4)),
        "emoji_freq": min(1.0, profile.emoji_freq),
        "catchphrases": catchphrases,
    }
    persona = {
        "name": target,
        "description": profile.to_style_prompt(),
        "core_anchors": anchors,
        "personality": {
            "warmth": max(0.1, min(1.0, 0.5 + positive * 0.35 - negative * 0.2)),
            "playfulness": max(0.1, min(1.0, 0.35 + profile.slang_freq + profile.emoji_freq)),
            "independence": 0.6,
            "jealousy": 0.3,
            "stubbornness": 0.4,
        },
        "speaking_style": speaking_style,
    }
    return {
        "persona": persona,
        "sample_count": len(conversations),
        "style_report": profile.to_dict(),
    }


# ═══════════════════════════════════════════════════════
# Clone Data Management API
# ═══════════════════════════════════════════════════════


@router.post("/api/clone/upload")
async def upload_clone_data(
    target: str = Query(..., min_length=1, max_length=128, description="被克隆者名称/wxid"),
    file: UploadFile = File(...),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """上传本地提取的微信聊天数据，服务器分析生成人设预览。

    架构：
    1. 用户在本地 Windows 电脑运行 wechat-decrypt 提取数据
    2. 导出为 JSON 文件（格式：[{"user": ..., "reply": ..., "timestamp": ...}, ...]）
    3. 通过本端点上传到服务器
    4. 服务器调用 StyleAnalyzer 分析，返回可编辑人设预览

    支持 wechat-decrypt 的导出格式，或简单的 [{user, reply}] 对话数组。
    """
    content = await file.read()
    max_size = 50 * 1024 * 1024  # 50MB
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail=f"文件大小超过限制 ({max_size // 1024 // 1024}MB)")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gbk", errors="replace")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}") from e

    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="数据必须是 JSON 数组格式")

    # 标准化对话格式（W7：带 is_self 的原生导出按方向定向，见函数 docstring）
    conversations, parse_errors, warnings, direction_stats = normalize_clone_conversations(data)

    if not conversations:
        hint = f"解析了 {len(data)} 条数据但无有效对话。"
        if parse_errors:
            hint += f" 错误：{'； '.join(parse_errors[:3])}"
        if direction_stats["self"] > 0 and direction_stats["target"] == 0:
            hint += (
                "检测到全部消息都是 is_self=true（你自己发的），没有任何被克隆者"
                "（is_self=false）的消息——无法从空目标侧分析说话风格，请检查导出是否选对了联系人。"
            )
        hint += " 支持格式：[{user, reply}] 或 wechat-decrypt 导出的 JSON（含 is_self/text）。"
        raise HTTPException(status_code=422, detail=hint)

    try:
        result = await asyncio.to_thread(
            _build_clone_preview_from_conversations, target.strip(), conversations
        )
        # 添加前 5 条对话预览
        result["preview"] = conversations[:5]
        result["direction_stats"] = direction_stats
        if warnings:
            result["warnings"] = warnings
        # 单侧数据明确提示（目标侧严重偏少时人设质量不可信）
        if direction_stats["target"] == 0:
            raise HTTPException(
                status_code=422,
                detail="数据中没有任何被克隆者（is_self=false）的消息，无法分析目标说话风格。",
            )
        logger.info(
            "克隆数据上传分析成功: target=%s conversations=%d chunks=%d direction=%s",
            target, len(conversations), result.get("sample_count", 0), direction_stats,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("克隆数据分析失败: %s", e)
        raise HTTPException(status_code=422, detail=f"分析失败: {e}") from e


@router.get("/api/clone/contacts")
async def list_clone_contacts(
    keyword: str = Query(default="", max_length=100),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    contacts = mgr.get_contacts(keyword=keyword)
    return {"contacts": contacts, "total": len(contacts)}


@router.get("/api/clone/datasets")
async def list_clone_datasets(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    datasets = mgr.list_datasets()
    return {"datasets": datasets, "total": len(datasets)}


@router.get("/api/clone/datasets/{person_id}")
async def get_clone_dataset_detail(
    person_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    only_user: bool = Query(default=False),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    return mgr.get_dataset_detail(
        person_id=person_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        only_user=only_user,
    )


@router.delete("/api/clone/datasets/{person_id}")
async def delete_clone_dataset(
    person_id: str,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    ok = mgr.delete_dataset(person_id)
    if not ok:
        raise HTTPException(404, f"数据集 {person_id} 未找到")
    return {"status": "deleted", "person_id": person_id}


@router.delete("/api/clone/datasets/{person_id}/conversation")
async def delete_clone_conversation(
    person_id: str,
    index: int = Query(..., description="对话索引（从0开始）"),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    ok = mgr.delete_conversation(person_id, index)
    if not ok:
        raise HTTPException(404, "对话未找到或删除失败")
    return {"status": "deleted", "person_id": person_id, "index": index}


@router.post("/api/clone/datasets/{person_id}/conversations/batch-delete")
async def batch_delete_clone_conversations(
    person_id: str,
    indices: list[int] = Query(..., description="要删除的索引列表"),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    deleted = mgr.batch_delete_conversations(person_id, indices)
    return {"status": "deleted", "person_id": person_id, "deleted_count": deleted}


@router.get("/api/clone/stats")
async def get_clone_stats(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    return mgr.get_stats()
