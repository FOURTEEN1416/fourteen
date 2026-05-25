"""
正文提取器 — 从网页提取正文内容
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("content_extractor")

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False
    logger.warning("trafilatura not installed, content extraction limited")


class ContentExtractor:
    """
    正文提取器

    使用trafilatura提取网页正文，失败时降级到newspaper3k
    """

    # 禁止访问的内网IP段
    BLOCKED_IP_RANGES = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("0.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
    ]

    # 禁止访问的端口
    BLOCKED_PORTS = [22, 23, 25, 3306, 5432, 6379, 27017]

    def __init__(self):
        self._fallback_enabled = True

    def _validate_url(self, url: str) -> tuple[bool, str]:
        """
        验证URL安全性，防止SSRF攻击

        Returns:
            (是否安全, 错误原因)
        """
        try:
            parsed = urlparse(url)

            # 只允许http和https协议
            if parsed.scheme not in ["http", "https"]:
                return False, f"不支持的协议: {parsed.scheme}"

            # 检查端口
            port = parsed.port
            if port and port in self.BLOCKED_PORTS:
                return False, f"禁止访问的端口: {port}"

            # 检查主机名
            hostname = parsed.hostname
            if not hostname:
                return False, "无效的主机名"

            # 禁止localhost
            if hostname.lower() in ["localhost", "localhost.localdomain"]:
                return False, "禁止访问localhost"

            # 尝试解析IP地址
            try:
                ip = ipaddress.ip_address(hostname)
                for blocked_range in self.BLOCKED_IP_RANGES:
                    if ip in blocked_range:
                        return False, f"禁止访问内网IP: {hostname}"
            except ValueError:
                # 不是IP地址，是域名，允许
                pass

            return True, ""

        except Exception as e:  # noqa: BLE001

            logger.debug("Error: %s", e)

            return False, f"URL解析失败: {e}"

    def extract(self, url: str) -> dict[str, Any]:
        """
        从URL提取正文

        Args:
            url: 网页URL

        Returns:
            {
                "title": 标题,
                "text": 正文,
                "success": 是否成功,
            }
        """
        # 安全验证：防止SSRF攻击
        is_safe, error_msg = self._validate_url(url)
        if not is_safe:
            logger.warning("URL validation failed: %s - %s", url, error_msg)
            return {"title": "", "text": "", "success": False, "error": error_msg}

        if not HAS_TRAFILATURA:
            return self._fallback_extract(url)

        try:
            import httpx

            # 获取网页内容
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                html_content = response.text

            # 使用trafilatura提取
            result = trafilatura.extract(
                html_content,
                include_comments=False,
                include_links=False,
                output_format="python",
            )

            if result:
                # 提取元数据
                metadata = trafilatura.extract_metadata(html_content)

                return {
                    "title": metadata.title if metadata else "",
                    "text": result,
                    "success": True,
                    "method": "trafilatura",
                }

            # trafilatura失败，尝试降级  # noqa: BLE001

            return self._fallback_extract(url)

        except Exception as e:  # noqa: BLE001

            logger.warning("Content extraction failed for %s: %s", url, e)
            return self._fallback_extract(url)

    def _fallback_extract(self, url: str) -> dict[str, Any]:
        """降级提取方法"""
        try:
            import newspaper
            article = newspaper.Article(url)
            article.download()
            article.parse()

            return {
                "title": article.title,
                "text": article.text,
                "success": True,
                "method": "newspaper3k",  # noqa: BLE001
            }  # noqa: BLE001

        except ImportError:
            logger.debug("newspaper3k not available")
            return {"title": "", "text": "", "success": False}
        except Exception as e:  # noqa: BLE001

            logger.warning("Fallback extraction failed: %s", e)
            return {"title": "", "text": "", "success": False}

    def extract_batch(self, urls: list[str]) -> list[dict[str, Any]]:
        """批量提取"""
        results = []
        for url in urls:
            result = self.extract(url)
            if result["success"]:
                results.append(result)
        return results
