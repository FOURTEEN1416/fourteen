"""
WeChat 通道适配器

为 REST API 提供 WeChat 通道的可用性检测和状态查询。
桥接到 wechat_direct 的 WeChatConnector，在系统运行时反映真实的微信连接状态。

设计原则：
- 零依赖导入：即使 wechat_direct 未初始化也不报错
- 透明桥接：状态信息直接来自运行时的 WeChatConnector 实例
- 轻量：不引入额外逻辑，只做状态透传
"""

import logging

logger = logging.getLogger("wechat.adapter")


class WeChatAdapter:
    """
    WeChat 通道适配器

    通过读取 wechat_direct.WeChatConnector 的运行时实例获取状态。
    如果 WeChatConnector 已初始化且已连接，则 wechat 可用。
    """

    @staticmethod
    def is_available() -> bool:
        """检测 WeChat 通道是否可用（wechat_direct 已初始化）"""
        try:
            from wechat_direct.wechat_connector import WeChatConnector
            # WeChatConnector 是单例模式，检查是否已有活跃实例
            return WeChatConnector.is_available()
        except (ImportError, AttributeError):
            return False
        except Exception as e:
            logger.debug("WeChat availability check failed: %s", e)
            return False

    @staticmethod
    def get_status() -> dict:
        """
        获取 WeChat 连接详细状态。

        Returns:
            dict: {
                "available": bool,    # 适配器是否就绪
                "connected": bool,    # 微信是否已连接
                "uptime_seconds": int,
                "reconnect_attempts": int,
                "missed_heartbeats": int,
                "messages_today": int,
                "last_activity": str,
            }
        """
        default = {
            "available": False,
            "connected": False,
            "uptime_seconds": 0,
            "reconnect_attempts": 0,
            "missed_heartbeats": 0,
            "messages_today": 0,
            "last_activity": "",
        }

        try:
            from wechat_direct.wechat_connector import WeChatConnector
            status = WeChatConnector.get_status()
            if status:
                return status
            return default
        except (ImportError, AttributeError):
            return default
        except Exception as e:
            logger.debug("WeChat status check failed: %s", e)
            return default
