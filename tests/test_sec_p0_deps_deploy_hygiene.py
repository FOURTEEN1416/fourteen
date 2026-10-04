"""P0 安全修复批 · 依赖与部署卫生域（红测先行，修复前必红）

覆盖任务书 F1–F6，断言对象全部为仓库内文本/JSON 实况（只读仓库文件，
不写 data/，不触网；F5 的运行时断言依赖本机已安装的 starlette）：

- F1  axios 锁定版本 ≥ 1.20.0（^1.7.0 语义内，修 21 条 advisory）
- F2  vite ≥ 8.0.16；react-router / react-router-dom ≥ 7.18.2
- F3  ci.yml frontend job 在依赖安装之后带
      ``npm audit --package-lock-only --audit-level=high`` 审计门（发现即非零退出）
- F4  client.ts 不再携带 VITE_API_KEY→X-API-Key 烧 key 链（09-19 裁决）；
      frontend/.gitignore 补 .env.development 与 .env.test
- F5  pyproject.toml 显式声明 starlette>=1.3.1（CVE-2026-54283 表单内存耗尽）
      且本机安装版本已对齐
- F6  nginx 两模板：/ws/ 段 access_log off、HTTP server 块三安全头（带 always）、
      /api/ 段补 X-Forwarded-Proto；service 模板 --workers 4 对齐生产实况
      + 注记生产 unit User=root 与模板 www-data 的漂移
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# F6 涉及的 nginx 模板（公开仓模板 + 本机对齐版各一份）
NGINX_TEMPLATES = [
    "deploy/nginx-ai-girlfriend.conf",
    "deploy/nginx/ai-girlfriend.conf",
]


def _read(rel: str) -> str:
    """读仓库内相对路径文本（只读）。"""
    return (REPO / rel).read_text(encoding="utf-8")


def _version_tuple(version: str) -> tuple[int, ...]:
    """'1.20.0' → (1, 20, 0)，供 >= 比较。"""
    return tuple(int(part) for part in version.split(".")[:3])


def _lock_version(pkg: str) -> tuple[int, ...]:
    """从 frontend/package-lock.json 取指定包的锁定版本。"""
    lock = json.loads(_read("frontend/package-lock.json"))
    entry = lock["packages"].get(f"node_modules/{pkg}")
    assert entry is not None, f"package-lock.json 缺少 {pkg}"
    return _version_tuple(entry["version"])


def _ci_frontend_job() -> str:
    """截取 ci.yml 中 frontend job 的文本段（到下一个同级 job 为止）。"""
    text = _read(".github/workflows/ci.yml")
    m = re.search(r"^  frontend:\n(.*?)(?=^  [\w-]+:\s*$)", text, re.S | re.M)
    assert m, "ci.yml 未找到 frontend job"
    return m.group(1)


def _server_block(conf: str) -> str:
    """取 nginx 配置首个 server 块的块内文本（花括号配平；注释内 ${VAR} 成对不扰）。"""
    start = conf.index("server {") + len("server {")
    depth = 1
    for i in range(start, len(conf)):
        if conf[i] == "{":
            depth += 1
        elif conf[i] == "}":
            depth -= 1
            if depth == 0:
                return conf[start:i]
    raise AssertionError("server 块未闭合")


def _location_block(conf: str, location: str) -> str:
    """取 nginx 配置指定 location 块的块内文本（花括号配平）。"""
    m = re.search(rf"^    location {re.escape(location)} \{{\n", conf, re.M)
    assert m, f"未找到 location {location}"
    depth = 1
    for i in range(m.end(), len(conf)):
        if conf[i] == "{":
            depth += 1
        elif conf[i] == "}":
            depth -= 1
            if depth == 0:
                return conf[m.end() : i]
    raise AssertionError(f"location {location} 块未闭合")


# ══════════════════ F1 axios ══════════════════


def test_f1_axios_lock_at_least_1_20_0():
    assert _lock_version("axios") >= (1, 20, 0), (
        "axios 锁定版本低于 1.20.0（21 条 advisory 未修）"
    )


# ══════════════════ F2 vite / react-router ══════════════════


def test_f2_vite_lock_at_least_8_0_16():
    assert _lock_version("vite") >= (8, 0, 16)


def test_f2_react_router_lock_at_least_7_18_2():
    assert _lock_version("react-router") >= (7, 18, 2)
    assert _lock_version("react-router-dom") >= (7, 18, 2)


# ══════════════════ F3 CI 依赖审计门 ══════════════════


def test_f3_frontend_job_has_npm_audit_gate():
    job = _ci_frontend_job()
    assert "npm audit --package-lock-only --audit-level=high" in job, (
        "frontend job 缺少 npm audit 依赖审计门"
    )


def test_f3_audit_gate_runs_after_install():
    job = _ci_frontend_job()
    assert "bun install" in job, "frontend job 缺少依赖安装步骤"
    assert job.index("npm audit") > job.index("bun install"), (
        "审计门必须放在依赖安装之后"
    )


# ══════════════════ F4 前端 key 烧入链 + gitignore ══════════════════


def test_f4_client_ts_no_api_key_baking():
    src = _read("frontend/src/api/client.ts")
    assert "VITE_API_KEY" not in src, (
        "client.ts 仍读取 VITE_API_KEY（构建可把 key 烧进 bundle）"
    )
    assert "X-API-Key" not in src, "client.ts 仍注入 X-API-Key 请求头"


def test_f4_frontend_gitignore_covers_env_development_and_test():
    lines = {line.strip() for line in _read("frontend/.gitignore").splitlines()}
    assert ".env.development" in lines, "frontend/.gitignore 缺 .env.development"
    assert ".env.test" in lines, "frontend/.gitignore 缺 .env.test"


# ══════════════════ F5 starlette 下限约束 ══════════════════


def test_f5_pyproject_declares_starlette_floor():
    text = _read("pyproject.toml")
    assert re.search(r'"starlette>=1\.3\.1"', text), (
        "pyproject.toml 未显式声明 starlette>=1.3.1（CVE-2026-54283）"
    )


def test_f5_installed_starlette_meets_floor():
    # 依赖本机环境：修复批要求 pip install -U "starlette>=1.3.1" 对齐；
    # CI 的 pip install -e ".[dev]" 读取同一约束后同样满足。
    import importlib.metadata

    installed = importlib.metadata.version("starlette")
    assert _version_tuple(installed) >= (1, 3, 1), (
        f"本机 starlette {installed} 低于 1.3.1（CVE-2026-54283 修复线）"
    )


# ══════════════════ F6 nginx 模板 + systemd unit 模板 ══════════════════


@pytest.mark.parametrize("rel", NGINX_TEMPLATES)
def test_f6_ws_location_disables_access_log(rel):
    assert "access_log off" in _location_block(_read(rel), "/ws/"), (
        f"{rel} 的 /ws/ 反代段未关闭 access_log（长连接请求刷爆日志）"
    )


@pytest.mark.parametrize("rel", NGINX_TEMPLATES)
def test_f6_api_location_forwards_proto(rel):
    assert "proxy_set_header X-Forwarded-Proto $scheme" in _location_block(
        _read(rel), "/api/"
    ), f"{rel} 的 /api/ 反代段未透传 X-Forwarded-Proto"


@pytest.mark.parametrize("rel", NGINX_TEMPLATES)
def test_f6_http_server_block_has_three_security_headers(rel):
    server = _server_block(_read(rel))
    for directive in (
        'add_header X-Frame-Options "SAMEORIGIN" always;',
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
    ):
        assert directive in server, f"{rel} HTTP server 块缺安全头：{directive}"


def test_f6_service_workers_aligned_to_production():
    text = _read("deploy/ai-girlfriend.service")
    assert "--workers 4" in text, "service 模板未对齐生产实况 4 worker"
    assert "--workers 1" not in text, "service 模板仍残留单 worker 配置"


def test_f6_service_notes_root_user_drift():
    text = _read("deploy/ai-girlfriend.service")
    assert "User=root" in text, (
        "service 模板未注记生产 unit 实况 User=root 与模板 www-data 的漂移"
    )
