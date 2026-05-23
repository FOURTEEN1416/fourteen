"""
cowagent_adapter 统一初始化入口

将 main.py 中散落的 wechat 模式初始化逻辑收拢为单个函数，
提供清晰的「启动微信通道」API，同时不破坏已有的直接调用方式。

用法:
    from cowagent_adapter.init import initialize_wechat_channel

    # 在 main.py 中:
    status = initialize_wechat_channel(
        orchestrator=orchestrator,
        heartbeat=True,
        cowagent_config="config/cowagent_config.json",
    )
    if status["ok"]:
        print(f"微信通道已启动, PID={status['pid']}")
"""

import logging
import multiprocessing
import os
from pathlib import Path
from typing import Any, Callable, Optional

from cowagent_adapter._globals import bot_registry

logger = logging.getLogger("cowagent.init")


# ── 适配器: 抹平两种编排器的差异 ──


class _OrchestratorAdapter:
    """
    编排器适配器 — 将任何符合 process_message(msg, session_id, ...) 签名的对象
    封装为一致的 reply(msg) → Reply 接口。

    支持两种编排器形态:
    - full 模式: orchestrator 有 _emotion, _memory, 直接调用 process_message
    - fast 模式: OptimizedOrchestrator, 同上
    """

    def __init__(self, orchestrator: Any):
        self._orch = orchestrator
        self._heartbeat: Optional[Any] = None

    def reply(self, user_msg: str, session_id: str = "") -> Any:
        """统一回复接口，抹平 full/fast 模式差异"""
        import asyncio
        if asyncio.iscoroutinefunction(self._orch.process_message):
            try:
                asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, self._orch.process_message(user_msg, session_id)).result()
            except RuntimeError:
                result = asyncio.run(self._orch.process_message(user_msg, session_id))
        else:
            result = self._orch.process_message(user_msg, session_id)
        return _Reply(result.get("reply", ""))

    def health_check(self) -> dict:
        """代理到编排器的 health_check（如果有）"""
        if hasattr(self._orch, "health_check"):
            return self._orch.health_check()
        return {"orchestrator": True}


class _Reply:
    """兼容 CowAgent Reply 的轻量包装"""

    def __init__(self, content: str):
        self.content = content


# ── 心跳管理 ──


class _HeartbeatManager:
    """
    心跳管理器 — 封装启动/停止/状态查询，便于统一管理。
    """

    def __init__(self, check_interval: int = 30, max_missed: int = 3):
        self._heartbeat: Optional[Any] = None
        self._check_interval = check_interval
        self._max_missed = max_missed
        self._listeners: list[Callable[[bool], None]] = []

    def start(self, adapter: _OrchestratorAdapter) -> bool:
        """启动心跳监控线程"""
        try:
            from cowagent_adapter.heartbeat import WeChatHeartbeat

            hb = WeChatHeartbeat(
                check_interval=self._check_interval,
                max_missed=self._max_missed,
            )

            # 注册状态变化回调
            def _on_change(connected: bool):
                if connected:
                    logger.info("💓 微信连接状态: 已连接")
                else:
                    logger.warning("💔 微信连接状态: 已断开")
                for cb in self._listeners:
                    try:
                        cb(connected)
                    except Exception:
                        pass

            hb.on_status_change(_on_change)

            # 挂到 adapter 上方便 REST API 读取
            hb.start(cowagent_instance=None)
            adapter._heartbeat = hb
            self._heartbeat = hb
            logger.info("心跳监控已启动 (interval=%ds)", self._check_interval)
            return True
        except ImportError as e:
            logger.warning("心跳监控未启用 (缺少 heartbeat 模块: %s)", e)
            return False
        except Exception as e:
            logger.warning("心跳监控启动失败: %s", e)
            return False

    def stop(self):
        if self._heartbeat:
            self._heartbeat.stop()
            self._heartbeat = None

    def on_status_change(self, callback: Callable[[bool], None]):
        self._listeners.append(callback)

    @property
    def status(self) -> dict:
        if self._heartbeat:
            return self._heartbeat.get_status()
        return {"connected": False, "uptime_seconds": 0}


# ── CowAgent 子进程管理 ──


class _CowAgentProcess:
    """
    CowAgent 子进程管理器 — 负责启动、监控、重启子进程。
    """

    def __init__(self):
        self._process: Optional[multiprocessing.Process] = None
        self._restart_count = 0
        self._max_restarts = 10

    def start(self, config_path: str = "") -> int:
        """启动 CowAgent 子进程，返回 PID（0 表示失败）"""
        if config_path:
            resolved = str(Path(config_path).resolve())
            if not os.path.exists(resolved):
                logger.warning("CowAgent 配置不存在: %s，使用默认", resolved)
            else:
                os.environ["COWAGENT_CONFIG"] = resolved

        # target 必须是模块级函数，不能是嵌套函数（Windows spawn 模式不能 pickle 嵌套函数）
        proc = multiprocessing.Process(
            target=_run_subprocess_target,
            daemon=True,
            name="cowagent-wechat",
        )
        proc.start()
        self._process = proc
        logger.info("CowAgent 子进程已启动 (PID=%d)", proc.pid)
        return proc.pid or 0

    def check(self) -> bool:
        """检查子进程是否存活，不存活则重启。返回 True 表示正常。"""
        if self._process is None:
            return False
        if self._process.is_alive():
            return True
        # 进程挂了，尝试重启
        if self._restart_count >= self._max_restarts:
            logger.error("CowAgent 已达最大重启次数 (%d)", self._max_restarts)
            return False
        self._restart_count += 1
        logger.warning(
            "CowAgent 进程已退出 (exitcode=%s)，第 %d 次重启...",
            self._process.exitcode,
            self._restart_count,
        )
        self.start()
        return False

    def stop(self):
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
            logger.info("CowAgent 子进程已终止")

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None


# ── 子进程入口（模块级函数，Windows spawn 模式下必须可 pickle） ──


def _run_subprocess_target():
    """
    CowAgent 子进程入口 — 在子进程中启动 cowagent_src/app.py。

    必须为模块级函数（不能嵌套在类/方法中），
    因为 Windows 的 multiprocessing spawn 模式需要 pickle 目标函数。
    """
    try:
        import sys
        from pathlib import Path

        # 硬编码路径（__file__ 在 spawn 子进程中不可靠）
        _project_root = r"C:\Users\FOUR\Desktop\ai-girlfriend"
        _cowagent_src = r"C:\Users\FOUR\Desktop\ai-girlfriend\cowagent_src"

        # cowagent_src/ 有自己的 common/（const.py 等模型常量），
        # 但项目根目录的 common/（有 __init__.py）会阻挡导入。
        # 把 cowagent_src/ 放 sys.path 最前面，移除项目根目录，
        # 这样 from common import const 会找到 cowagent_src/common/const.py。
        if _cowagent_src in sys.path:
            sys.path.remove(_cowagent_src)
        sys.path.insert(0, _cowagent_src)

        # 移除项目根目录（main.py:36 的 sys.path.insert 以及 PYTHONPATH 会加回来）
        _root_norm = str(Path(_project_root).resolve())
        sys.path = [p for p in sys.path if str(Path(p).resolve()) != _root_norm]

        import cowagent_src.app as cowapp
        cowapp.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        import traceback
        logger.error("CowAgent 子进程异常: %s\n%s", e, traceback.format_exc())
        raise


# ── 统一初始化入口 ──


def initialize_wechat_channel(
    orchestrator: Any,
    *,
    enable_heartbeat: bool = True,
    heartbeat_interval: int = 30,
    heartbeat_max_missed: int = 3,
    cowagent_config: str = "",
    auto_restart: bool = True,
) -> dict:
    """
    完整初始化微信通道 — 方案 C 的核心入口。

    执行步骤:
    1. 将 orchestrator 包装为 _OrchestratorAdapter
    2. 注册到 bot_registry（供 REST API 和 WeChatAdapter 读取）
    3. 给 CowAgent 打补丁（patch_cowagent）
    4. 可选：启动心跳监控
    5. 启动 CowAgent 子进程（扫码登录微信）

    Args:
        orchestrator: 编排器实例（需要有 process_message 方法）
        enable_heartbeat: 是否启用心跳监控（默认: True）
        heartbeat_interval: 心跳检测间隔秒数（默认: 30）
        heartbeat_max_missed: 最大容忍心跳丢失次数（默认: 3）
        cowagent_config: CowAgent 配置文件路径（可选）
        auto_restart: 子进程退出后是否自动重启（默认: True）

    Returns:
        dict: {
            "ok": bool,
            "adapter": _OrchestratorAdapter,
            "heartbeat": _HeartbeatManager | None,
            "process": _CowAgentProcess | None,
            "pid": int | None,
            "message": str,
        }
    """
    result: dict = {
        "ok": False,
        "adapter": None,
        "heartbeat": None,
        "process": None,
        "pid": None,
        "message": "",
    }

    try:
        # ── Step 1: 包装编排器 ──
        adapter = _OrchestratorAdapter(orchestrator)
        result["adapter"] = adapter

        # ── Step 2: 注册到 bot_registry ──
        bot_registry.register(adapter)
        # 兼容 GirlfriendBot 全局引用（patch.py 会检查这个）
        from cowagent_adapter._globals import _girlfriend_bot_instance
        _girlfriend_bot_instance = adapter  # noqa: F811

        logger.info("编排器已注册到 bot_registry")

        # ── Step 3: CowAgent 打补丁 ──
        from cowagent_adapter.patch import patch_cowagent
        patch_cowagent()

        # ── Step 4: 心跳监控（可选） ──
        hb_mgr: Optional[_HeartbeatManager] = None
        if enable_heartbeat:
            hb_mgr = _HeartbeatManager(
                check_interval=heartbeat_interval,
                max_missed=heartbeat_max_missed,
            )
            ok = hb_mgr.start(adapter)
            if not ok:
                logger.warning("心跳监控启动失败，继续启动...")
            result["heartbeat"] = hb_mgr

        # ── Step 5: 启动 CowAgent 子进程 ──
        proc_mgr = _CowAgentProcess()
        pid = proc_mgr.start(cowagent_config)
        result["process"] = proc_mgr
        result["pid"] = pid

        if pid:
            result["ok"] = True
            result["message"] = f"微信通道已启动 (PID={pid})"
            logger.info("✅ 微信通道初始化完成 (PID=%d)", pid)
        else:
            result["message"] = "CowAgent 子进程启动失败"
            logger.warning("⚠️ CowAgent 子进程未能启动")

        return result

    except ImportError as e:
        import traceback
        msg = f"初始化微信通道失败: 缺少依赖模块 — {e}"
        logger.warning(msg)
        logger.warning("Traceback:\n%s", traceback.format_exc())
        # 写入调试文件
        try:
            with open(r"C:\Users\FOUR\AppData\Local\Temp\opencode\import_error_debug.txt", "w", encoding="utf-8") as _f:
                _f.write(f"Error: {e}\n")
                _f.write(f"Traceback:\n{traceback.format_exc()}\n")
                import sys as _sys
                _f.write(f"\nsys.path: {_sys.path}\n")
                _f.write(f"\nsys.modules keys (common*): {[k for k in _sys.modules if 'common' in k]}\n")
        except Exception:
            pass
        result["message"] = msg
        return result

    except Exception as e:
        msg = f"初始化微信通道异常: {e}"
        logger.exception(msg)
        result["message"] = msg
        return result


# ── 简化版: 只注册不启动微信（用于纯 REST API 场景） ──


def register_standalone(
    orchestrator: Any,
    *,
    enable_heartbeat: bool = True,
) -> dict:
    """
    轻量初始化 — 不启动 CowAgent 子进程，只注册编排器并可选启动心跳。

    适用于：
    - 仅使用 REST API，不需要微信通道
    - 测试环境
    - 前端控制台模式

    Args:
        orchestrator: 编排器实例
        enable_heartbeat: 是否启动假心跳（占位）

    Returns:
        dict: {"ok": bool, "adapter": _OrchestratorAdapter, ...}
    """
    result: dict = {"ok": False, "adapter": None, "message": ""}

    try:
        adapter = _OrchestratorAdapter(orchestrator)
        result["adapter"] = adapter

        bot_registry.register(adapter)
        from cowagent_adapter._globals import _girlfriend_bot_instance
        _girlfriend_bot_instance = adapter  # noqa: F811

        if enable_heartbeat:
            hb_mgr = _HeartbeatManager()
            hb_mgr.start(adapter)
            result["heartbeat"] = hb_mgr

        result["ok"] = True
        result["message"] = "编排器已注册（无微信通道）"
        logger.info("编排器独立注册完成")

        return result

    except Exception as e:
        result["message"] = f"独立注册失败: {e}"
        logger.exception("独立注册异常")
        return result
