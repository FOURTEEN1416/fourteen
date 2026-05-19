#!/usr/bin/env python3
"""
AI 伴侣女友"小暖" — 主入口 (CowAgent 集成版)

启动流程：
1. 加载配置
2. 初始化角色引擎（PersonaEngine）
3. 初始化记忆管线（MemoryPipeline）
4. 初始化主动消息引擎（ASEEngine + Scheduler）
5. 注册 GirlfriendBot 到 CowAgent
6. 启动 CowAgent（微信聊天通道 / 控制台模式）

使用方式：
    python main.py                  # 完整启动（微信 + 主动消息）
    python main.py --no-wechat      # 控制台聊天模式
    python main.py --no-scheduler   # 仅微信（无主动消息）
    python main.py --init-only      # 仅初始化（测试）
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
import time
from pathlib import Path

# 确保项目目录在路径中
project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            str(project_root / "data" / "app.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI伴侣女友 - 小暖")
    parser.add_argument("--no-wechat", action="store_true",
                       help="不启动微信通道（控制台聊天模式）")
    parser.add_argument("--no-scheduler", action="store_true",
                       help="不启动主动消息调度器")
    parser.add_argument("--config", type=str, default="config",
                       help="配置文件目录")
    parser.add_argument("--init-only", action="store_true",
                       help="仅初始化（用于测试）")
    parser.add_argument("--clone", type=str, default=None,
                       help="克隆目标（wxid/文件路径），触发风格克隆管线")
    parser.add_argument("--clone-source", type=str, default="wcf",
                       choices=["wcf", "wechatmsg", "decrypt", "txt", "csv", "json"],
                       help="克隆数据来源 (decrypt = 微信4.x数据库解密)")
    parser.add_argument("--clone-name", type=str, default="",
                       help="被克隆者名称")
    return parser.parse_args()


def print_banner() -> None:
    """打印启动横幅"""
    banner = """
    ╔══════════════════════════════════╗
    ║      💕 小暖 — AI 伴侣女友      ║
    ║        v1.1 · CowAgent 集成     ║
    ╚══════════════════════════════════╝
    """
    print(banner)


def main():
    args = parse_args()
    print_banner()

    # ── 克隆模式：独立运行克隆管线后退出 ──
    if args.clone:
        _run_clone_pipeline(args)
        return

    # ── 1. 配置加载 ──
    from my_character import ConfigLoader, PersonaEngine, EmotionEngine, ToneMimic

    logger.info("[1/6] 加载配置...")
    config_loader = ConfigLoader(config_dir=args.config)
    persona_config = config_loader.load_persona()
    emotion_config = config_loader.load_emotion()
    logger.info("      角色: %s", persona_config.get("name", "小暖"))

    # ── 2. 初始化角色引擎 ──
    logger.info("[2/6] 初始化角色引擎...")
    emotion_engine = EmotionEngine(config=emotion_config)
    tone_mimic = ToneMimic(chroma_path=str(project_root / "data" / "chroma_db"))
    persona = PersonaEngine(
        config_loader=config_loader,
        emotion_engine=emotion_engine,
        tone_mimic=tone_mimic,
    )
    logger.info("      情感初始状态: %s", emotion_engine)

    # ── 3. 初始化 LLM 网关 ──
    logger.info("[3/6] 初始化 LLM 网关...")
    from llm_provider import get_llm
    llm = get_llm()
    health = llm.health_check()
    provider_name = health.get("provider", "unknown")
    model_name = health.get("default_model", llm.model)
    if health.get("configured", False) and health.get("reachable", True):
        logger.info("      ✅ %s 已就绪 (model=%s)", provider_name, model_name)
    else:
        logger.info("      ⚠️  LLM 未完整配置，将使用模拟回复")

    # ── 4. 初始化记忆管线 ──
    logger.info("[4/6] 初始化记忆系统...")
    from memory import (
        VectorMemory, StructuredMemory,
        FactExtractor, DiarySummarizer, MemoryPipeline,
    )

    vector_memory = VectorMemory(
        chroma_path=str(project_root / "data" / "chroma_db"),
    )
    structured_memory = StructuredMemory(
        db_path=str(project_root / "data" / "sqlite.db"),
    )
    fact_extractor = FactExtractor()
    diary_summarizer = DiarySummarizer()

    memory_pipeline = MemoryPipeline(
        vector_memory=vector_memory,
        structured_memory=structured_memory,
        fact_extractor=fact_extractor,
        diary_summarizer=diary_summarizer,
        emotion_engine=emotion_engine,
    )
    logger.info("      今日聊天: %d 条", structured_memory.count_chats_today())

    # ── 5. 初始化主动消息系统 ──
    scheduler = None
    ase_engine = None
    if not args.no_scheduler:
        logger.info("[5/6] 初始化主动消息系统...")

        import cowagent_adapter._globals as gl
        from proactive import ASEEngine, ProactiveScheduler

        ase_engine = ASEEngine(
            affinity_level_func=lambda: emotion_engine.state.affinity,
        )

        def send_message(msg: str) -> None:
            """发送主动消息"""
            if args.no_wechat:
                print(f"\n💕 [小暖主动] {msg}")
            else:
                # 通过 GirlfriendBot 发送到微信
                try:
                    if gl._girlfriend_bot_instance:
                        to_user = gl._girlfriend_bot_instance.WECHAT_CONTACT_ID or ""
                        if to_user:
                            gl._girlfriend_bot_instance.send_message(to_user, msg)
                        else:
                            logger.warning("主动消息: 未设置微信联系人ID，消息未发送: %s", msg[:50])
                    else:
                        logger.warning("主动消息: GirlfriendBot 未初始化，消息未发送")
                except Exception as e:
                    logger.error("主动消息发送异常: %s", e)
            logger.info("[ACTIVE] %s", msg)

        def daily_maint() -> None:
            summary = memory_pipeline.daily_maintenance()
            if summary:
                logger.info("每日摘要: %s", summary)

        scheduler = ProactiveScheduler(
            ase_engine=ase_engine,
            send_message_func=send_message,
            daily_maintenance_func=daily_maint,
        )
        logger.info("      主动消息引擎已就绪")
    else:
        logger.info("[5/6] 主动消息系统已禁用 (--no-scheduler)")

    # ── 6. 启动聊天通道 ──
    logger.info("[6/6] 启动聊天通道...")

    components = {
        "ConfigLoader": config_loader,
        "EmotionEngine": emotion_engine,
        "ToneMimic": tone_mimic,
        "PersonaEngine": persona,
        "VectorMemory": vector_memory,
        "StructuredMemory": structured_memory,
        "MemoryPipeline": memory_pipeline,
        "LLM": llm,
    }
    if ase_engine:
        components["ASEEngine"] = ase_engine

    health_check_all(components)

    if scheduler:
        try:
            scheduler.start()
            logger.info("调度器已启动")
        except Exception as e:
            logger.warning("调度器启动失败: %s", e)

    if args.init_only:
        logger.info("--init-only 模式，初始化完成")
        return

    if args.no_wechat:
        # 控制台聊天模式
        _run_console_chat(persona, emotion_engine, memory_pipeline, tone_mimic, llm, ase_engine)
    else:
        # 微信模式 — 通过 CowAgent
        _run_wechat_chat(persona, emotion_engine, memory_pipeline, tone_mimic, llm, ase_engine, scheduler)


def _run_console_chat(persona, emotion_engine, memory_pipeline, tone_mimic, llm, ase_engine):
    """控制台聊天模式 — 直接输入消息"""
    print("\n" + "=" * 50)
    print("  控制台聊天模式 — 输入消息直接聊天")
    print("  输入 /quit 退出  /reset 重置  /status 查看状态")
    print("=" * 50 + "\n")

    from cowagent_adapter import GirlfriendBot

    # 创建 GirlfriendBot（复用管线）
    bot = GirlfriendBot(
        persona_engine=persona,
        emotion_engine=emotion_engine,
        memory_pipeline=memory_pipeline,
        tone_mimic=tone_mimic,
        llm=llm,
        ase_engine=ase_engine,
    )

    try:
        while True:
            try:
                query = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 下次再来找我哦～")
                break

            if not query:
                continue

            if query == "/quit":
                print("👋 笨蛋，记得想我！")
                break
            elif query == "/reset":
                memory_pipeline.structured_memory.clear_session("console")
                print("✅ 记忆已重置")
                continue
            elif query == "/status":
                state = emotion_engine.state
                print(f"  情感: {state.emotion} | 能量: {state.energy:.1f} | 好感度: {state.affinity:.1f}")
                print(f"  对话轮次: {memory_pipeline.structured_memory.count_chats_today()} 条")
                continue

            reply = bot.reply(query)
            print(f"小暖 > {reply.content}")

    except Exception as e:
        logger.exception("控制台聊天异常")


def _run_wechat_chat(persona, emotion_engine, memory_pipeline, tone_mimic, llm, ase_engine, scheduler):
    """微信聊天模式 — CowAgent 运行在子进程中，主进程不崩溃"""
    from cowagent_adapter import patch_cowagent, GirlfriendBot
    import cowagent_adapter._globals as gl

    # 1. 创建 GirlfriendBot（主进程持有引用）
    bot = GirlfriendBot(
        persona_engine=persona,
        emotion_engine=emotion_engine,
        memory_pipeline=memory_pipeline,
        tone_mimic=tone_mimic,
        llm=llm,
        ase_engine=ase_engine,
    )

    # 2. 注册全局引用（patch.py 通过它获取 bot 实例）
    gl.bot_registry.register(bot)
    gl._girlfriend_bot_instance = bot

    # 3. 打补丁
    patch_cowagent()

    # 4. 启动心跳监控（在主进程中运行）
    from cowagent_adapter.heartbeat import WeChatHeartbeat
    heartbeat = WeChatHeartbeat(check_interval=30, max_missed=3)

    def on_heartbeat_change(connected: bool):
        if connected:
            logger.info("💓 微信连接状态: 已连接")
        else:
            logger.warning("💔 微信连接状态: 已断开")

    heartbeat.on_status_change(on_heartbeat_change)
    # 把 heartbeat 挂在 bot 上，方便 API 读取状态
    bot._heartbeat = heartbeat

    # 5. 在子进程中运行 CowAgent
    wechat_process: Optional[multiprocessing.Process] = None

    def start_cowagent_process():
        """启动 CowAgent 子进程"""
        nonlocal wechat_process

        config_path = str(project_root / "config" / "cowagent_config.json")
        os.environ["COWAGENT_CONFIG"] = config_path

        def _run_in_subprocess():
            """在子进程中运行的 CowAgent 入口"""
            import cowagent_src.app as cowapp
            try:
                cowapp.run()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                logger.error("CowAgent 子进程异常: %s", e)
                raise

        wechat_process = multiprocessing.Process(
            target=_run_in_subprocess,
            daemon=True,
            name="cowagent-wechat",
        )
        wechat_process.start()
        logger.info("CowAgent 子进程已启动 (PID=%d)", wechat_process.pid)

    def check_cowagent_process():
        """检查子进程状态，如果挂了则自动重启"""
        nonlocal wechat_process
        if wechat_process is None:
            return
        if not wechat_process.is_alive():
            logger.warning("CowAgent 子进程已退出 (exitcode=%s)，正在重启...", wechat_process.exitcode)
            start_cowagent_process()

    # 6. 启动
    print("\n📱 微信模式启动中...")
    print("   请扫描二维码登录微信个人号")
    print("   或按 Ctrl+C 切换回控制台模式\n")

    start_cowagent_process()
    heartbeat.start(cowagent_instance=None)

    # 7. 主进程保持运行，定期检查子进程状态
    try:
        while True:
            time.sleep(10)
            check_cowagent_process()
    except KeyboardInterrupt:
        print("\n👋 正在停止...")
        heartbeat.stop()
        if wechat_process and wechat_process.is_alive():
            wechat_process.terminate()
            wechat_process.join(timeout=5)
            logger.info("CowAgent 子进程已终止")


def health_check_all(components: dict) -> bool:
    """全组件健康检查"""
    all_ok = True
    print("\n[健康检查]")
    for name, component in components.items():
        if hasattr(component, "health_check"):
            try:
                status = component.health_check()
                ok = True
                if isinstance(status, dict):
                    ok = not any(
                        v is False for v in status.values()
                    )
                print(f"  {'✅' if ok else '❌'} {name}")
                if not ok:
                    logger.warning("%s health check failed: %s", name, status)
                    all_ok = False
            except Exception as e:
                print(f"  ❌ {name} (error: {e})")
                all_ok = False
        else:
            print(f"  ✅ {name} (no check)")

    if all_ok:
        print("\n  ✅ 全部通过\n")
    else:
        print("\n  ⚠️ 部分组件异常\n")

    return all_ok


def _run_clone_pipeline(args):
    """执行风格克隆管线"""
    print("\n" + "=" * 50)
    print("  🧬 风格克隆管线")
    print("=" * 50)
    print(f"  目标: {args.clone}")
    print(f"  来源: {args.clone_source}")
    if args.clone_name:
        print(f"  名称: {args.clone_name}")
    print()

    from weclone_adapter import WeCloneAdapter

    adapter = WeCloneAdapter(
        data_dir=str(project_root / "data" / "clone"),
        output_dir=str(project_root / "data" / "training"),
    )

    health = adapter.health_check()
    print(f"  训练器: {'可用' if health['trainer_available'] else '未安装(跳过训练)'}")
    print(f"  量化: {'可用' if health['quant_available'] else '未安装'}")
    print()

    result = adapter.clone(
        target=args.clone,
        source=args.clone_source,
        name=args.clone_name or None,
        do_train=health["trainer_available"],
    )

    if result.get("error"):
        print(f"  ❌ 克隆失败: {result['error']}")
        return

    print(f"\n{'='*50}")
    print(f"  ✅ 克隆完成")
    print(f"{'='*50}")
    print(f"  提取对话: {result.get('extracted_turns', 0)} 轮")
    print(f"  风格独特性: {result.get('uniqueness', 0):.0%}")
    if result.get("lora_path"):
        print(f"  LoRA 模型: {result['lora_path']}")
    print(f"  ToneMimic 注入: {result.get('injected_to_tone_mimic', 0)} 条")
    print()


if __name__ == "__main__":
    main()
