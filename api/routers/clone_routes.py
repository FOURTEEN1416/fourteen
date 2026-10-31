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

归属（2026-10-30 风格提取数据集 per-user 化）：
- 数据集面按 owner 分区（`data/clone/<user_id>/`），LIST/DETAIL/DELETE 一律
  只在该主体自己的目录内生效；他人数据集与不存在**同码 404**（防枚举）。
- 归属**只由服务端从认证主体派生**（`_owner_scope`），本文件不提供任何
  客户端自报归属的参数。
- 机器面（无 Bearer，**必须显式携带有效 X-API-Key**）/ admin → 根目录全量
  视图，**存量扁平数据 = 无主**，仅这两个面可见（不猜归属，D15 显式声明）。
  2026-10-31（R3-1）：机器面准入不再依赖 `API_KEY_ENABLED` 开关——该开关关闭时
  `verify_api_key_dep` 对匿名请求放行，若沿用「无主体 = 机器面」会让匿名者
  看到/删除全部存量数据集；现改为「登录主体 或 有效 API Key」二选一，见
  `_require_clone_access`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Security, UploadFile

from api.auth import verify_api_key_dep, verify_machine_api_key
from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.deps import deps
from voice.clone_data_manager import safe_person_id

logger = logging.getLogger("api.routers.clone_routes")

router = APIRouter(tags=["clone"])

# 单文件上限（既有契约：413）
_MAX_UPLOAD_SIZE = 50 * 1024 * 1024
# 单用户数据集数量上限（与上传端点的落盘校验同处；可用 env 覆盖）
_DEFAULT_MAX_DATASETS_PER_USER = 20


def _as_principal(candidate: Any) -> AuthPrincipal | None:
    """归一主体：直呼 handler（既有脚本/测试）会拿到 Depends 哨兵——按「无主体」解释。"""
    return candidate if isinstance(candidate, AuthPrincipal) else None


async def _require_clone_access(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    principal: AuthPrincipal | None = Depends(get_optional_principal),
) -> AuthPrincipal | None:
    """克隆面准入：登录主体 **或** 显式持有效 X-API-Key 的机器调用。

    R3-1 根因：`verify_api_key_dep` 在 ``API_KEY_ENABLED=false`` 时对无凭证请求
    直接放行（`if not enabled: return True` 分支），机器面与匿名面无法区分；
    而本文件的
    `_owner_scope(None)` 恰好把「无主体」解释为根目录全量视图。两者叠加 =
    显式关闭认证的部署里匿名者可 LIST/DETAIL/DELETE 全部存量数据集。

    本依赖做**独立于全局开关**的第二道判定：无登录主体时，必须携带与已配置
    API Key 匹配的 ``X-API-Key``（脚本/CI 机器调用照旧可用，前提是带 key）；
    否则 401。无效/过期 Bearer 由 `get_optional_principal` 直接 401，不会退化
    成机器面。
    """
    if principal is not None:
        return principal
    if verify_machine_api_key(x_api_key):
        return None
    raise HTTPException(
        status_code=401,
        detail="克隆 API 需要登录主体或有效的 X-API-Key",
        headers={"X-Error-Code": "AUTH_ERROR"},
    )


def _owner_scope(principal: AuthPrincipal | None) -> str:
    """归属作用域（服务端派生，客户端自报一律不看）。

    - 机器面（无 Bearer）/ admin → ``""``：根目录全量视图（含存量无主数据），
      保持 admin 与既有机器调用的兼容；
    - 普通登录用户 → 自己的 user_id 分区。
    """
    if principal is None or principal.role == "admin":
        return ""
    return str(principal.user_id)


def _safe_id_or_400(person_id: str) -> str:
    """person_id 路径段校验（与 manager 内部防护同一真源），非法 → 400。"""
    try:
        return safe_person_id(person_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _max_datasets_per_user() -> int:
    """单用户数据集上限（env `CLONE_MAX_DATASETS_PER_USER` 可配，脏值回落默认）。"""
    raw = os.environ.get("CLONE_MAX_DATASETS_PER_USER", "").strip()
    try:
        limit = int(raw)
    except ValueError:
        return _DEFAULT_MAX_DATASETS_PER_USER
    return limit if limit > 0 else _DEFAULT_MAX_DATASETS_PER_USER


def _parse_indices(raw_indices: list[str]) -> list[int]:
    """批量删除索引解析：同时接受「重复参数」与「逗号分隔」两种形态。

    前端 `cloneBatchDeleteConversations` 以 ``indices=0,1`` 逗号串传参，而
    FastAPI 的 ``list[int]`` 只认重复参数（``indices=0&indices=1``）——旧契约
    直接 422 int_parsing（当前无页面消费，属潜伏契约不一致）。此处统一解析，
    非法/空索引 → 400。数字形态（重复参数）保持向后兼容。
    """
    parsed: list[int] = []
    for raw in raw_indices:
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                parsed.append(int(part))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"非法索引: {part!r}") from exc
    if not parsed:
        raise HTTPException(status_code=400, detail="indices 不能为空")
    return parsed


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
    persist: bool = Query(
        default=False,
        description="为真时把标准化后的对话落盘到本人数据集分区（默认仅分析不落盘）",
    ),
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Depends(_require_clone_access),
):
    """上传本地提取的微信聊天数据，服务器分析生成人设预览。

    架构：
    1. 用户在本地 Windows 电脑运行 wechat-decrypt 提取数据
    2. 导出为 JSON 文件（格式：[{"user": ..., "reply": ..., "timestamp": ...}, ...]）
    3. 通过本端点上传到服务器
    4. 服务器调用 StyleAnalyzer 分析，返回可编辑人设预览

    支持 wechat-decrypt 的导出格式，或简单的 [{user, reply}] 对话数组。

    归属（2026-10-30 per-user 化）：
    - 默认 ``persist=False`` = 纯内存分析 + 预览（不落盘，现状零行为变更）；
    - ``persist=True`` = 把标准化后的对话写到 ``data/clone/<owner>/<target>_raw.json``
      —— 这是「每用户独立数据集」唯一的服务端创建路径，owner 由认证主体派生。
    - target 走路径段校验（禁 ``/``、``\\``、``..``），非法 400；单用户数据集
      数量超过上限 429（上限可经 ``CLONE_MAX_DATASETS_PER_USER`` 配置）。
    """
    # target 路径段校验先于一切落盘/读盘动作（复用 manager 的同一防护真源）
    try:
        safe_target = safe_person_id(target.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not safe_target:
        raise HTTPException(status_code=400, detail="target 不能为空")

    content = await file.read()
    max_size = _MAX_UPLOAD_SIZE
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
            _build_clone_preview_from_conversations, safe_target, conversations
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
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("克隆数据分析失败: %s", e)
        raise HTTPException(status_code=422, detail=f"分析失败: {e}") from e

    # 落盘（分析通过后才写，失败不留半成品）
    owner = _owner_scope(_as_principal(principal))
    persisted = False
    if persist:
        mgr = deps.get_clone_mgr()
        if owner and not mgr.has_dataset(safe_target, owner):
            limit = _max_datasets_per_user()
            if mgr.count_datasets(owner) >= limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"数据集数量已达上限（{limit} 个），请先删除旧数据集",
                )
        try:
            mgr.save_dataset(safe_target, conversations, owner=owner)
        except OSError as exc:
            logger.exception("克隆数据集落盘失败: target=%s owner=%r", safe_target, owner)
            raise HTTPException(status_code=500, detail=f"数据集保存失败: {exc}") from exc
        persisted = True

    result["persisted"] = persisted
    if persisted:
        result["person_id"] = safe_target
    logger.info(
        "克隆数据上传分析成功: target=%s conversations=%d chunks=%d direction=%s persisted=%s",
        safe_target, len(conversations), result.get("sample_count", 0), direction_stats, persisted,
    )
    return result


@router.get("/api/clone/contacts")
async def list_clone_contacts(
    keyword: str = Query(default="", max_length=100),
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Depends(_require_clone_access),
):
    """联系人列表：普通用户只见本人分区；admin / 机器面为根目录全量视图。"""
    mgr = deps.get_clone_mgr()
    contacts = mgr.get_contacts(keyword=keyword, owner=_owner_scope(_as_principal(principal)))
    return {"contacts": contacts, "total": len(contacts)}


@router.get("/api/clone/datasets")
async def list_clone_datasets(
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Depends(_require_clone_access),
):
    """数据集列表：普通用户只见本人分区；admin / 机器面为根目录全量视图。"""
    mgr = deps.get_clone_mgr()
    datasets = mgr.list_datasets(owner=_owner_scope(_as_principal(principal)))
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
    principal: AuthPrincipal | None = Depends(_require_clone_access),
):
    """数据集详情：归属分区内查找；找不到与他人数据集同码 404（防枚举）。"""
    mgr = deps.get_clone_mgr()
    owner = _owner_scope(_as_principal(principal))
    safe_id = _safe_id_or_400(person_id)
    result = mgr.get_dataset_detail(
        person_id=safe_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        only_user=only_user,
        owner=owner,
    )
    error = result.get("error")
    if error:
        if error == "数据集不存在":
            raise HTTPException(status_code=404, detail=f"数据集 {person_id} 未找到")
        raise HTTPException(status_code=422, detail=str(error))
    return result


@router.delete("/api/clone/datasets/{person_id}")
async def delete_clone_dataset(
    person_id: str,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Depends(_require_clone_access),
):
    """删除数据集：只在自己的归属分区内生效；他人数据集 / 不存在一律 404。"""
    mgr = deps.get_clone_mgr()
    owner = _owner_scope(_as_principal(principal))
    safe_id = _safe_id_or_400(person_id)
    ok = mgr.delete_dataset(safe_id, owner=owner)
    if not ok:
        raise HTTPException(404, f"数据集 {person_id} 未找到")
    return {"status": "deleted", "person_id": person_id}


@router.delete("/api/clone/datasets/{person_id}/conversation")
async def delete_clone_conversation(
    person_id: str,
    index: int = Query(..., description="对话索引（从0开始）"),
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Depends(_require_clone_access),
):
    """删除单条对话：只在自己的归属分区内生效；找不到 / 他人数据集一律 404。"""
    mgr = deps.get_clone_mgr()
    owner = _owner_scope(_as_principal(principal))
    safe_id = _safe_id_or_400(person_id)
    ok = mgr.delete_conversation(safe_id, index, owner=owner)
    if not ok:
        raise HTTPException(404, "对话未找到或删除失败")
    return {"status": "deleted", "person_id": person_id, "index": index}


@router.post("/api/clone/datasets/{person_id}/conversations/batch-delete")
async def batch_delete_clone_conversations(
    person_id: str,
    # list[str] 而非 list[int]：兼容前端 `indices=0,1` 逗号串与重复参数两种形态，
    # 具体整数校验/错误码由 _parse_indices 统一负责（非法 → 400）。
    indices: list[str] = Query(..., description="要删除的索引列表（支持重复参数或逗号分隔）"),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Depends(_require_clone_access),
):
    """批量删除对话：只在自己的归属分区内生效；他人数据集 / 不存在一律 404。"""
    mgr = deps.get_clone_mgr()
    owner = _owner_scope(_as_principal(principal))
    safe_id = _safe_id_or_400(person_id)
    parsed_indices = _parse_indices(indices)
    if not mgr.has_dataset(safe_id, owner=owner):
        raise HTTPException(404, f"数据集 {person_id} 未找到")
    deleted = mgr.batch_delete_conversations(safe_id, parsed_indices, owner=owner)
    return {"status": "deleted", "person_id": person_id, "deleted_count": deleted}


@router.get("/api/clone/stats")
async def get_clone_stats(
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Depends(_require_clone_access),
):
    """统计：普通用户只统计本人分区；admin / 机器面为根目录全量视图。"""
    mgr = deps.get_clone_mgr()
    return mgr.get_stats(owner=_owner_scope(_as_principal(principal)))

