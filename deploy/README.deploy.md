# 唯一的你 — 生产部署指南

> 预计阅读时间：10 分钟 · 部署时间：15-30 分钟

## 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Ubuntu | 22.04+ | 推荐 24.04 LTS |
| Python | 3.10 - 3.12 | 3.12 推荐 |
| PostgreSQL | 15+ | 可选：默认运行时是 SQLite（data/users.db）；启用 PG 需自行安装 asyncpg |
| Node.js | 22 LTS | 构建前端 |
| Nginx | 1.24+ | 反向代理 |
| RAM | ≥ 4GB | 推荐 8GB |
| CPU | ≥ 2 核 | 推荐 4 核 |
| 磁盘 | ≥ 20GB | SSD 推荐 |

---

## 快速部署（5 步）

### 第一步：服务器初始化

```bash
# SSH 登录后
sudo apt-get update && sudo apt-get upgrade -y
sudo timedatectl set-timezone Asia/Shanghai

# 克隆项目
sudo mkdir -p /opt/ai-girlfriend
sudo chown $USER:$USER /opt/ai-girlfriend
git clone https://github.com/FOURTEEN1416/fourteen.git /opt/ai-girlfriend
cd /opt/ai-girlfriend
```

### 第二步：运行初始化脚本

```bash
sudo bash deploy/setup.sh
```

脚本自动完成：

- ✅ 安装系统依赖（Python, Nginx, Node.js, certbot；setup.sh 同时安装并初始化 PostgreSQL）
- ✅ 创建虚拟环境并安装 Python 依赖
- ✅ 创建 PostgreSQL 数据库和用户（默认 SQLite 部署可不填 DATABASE_URL）
- ✅ 配置 Nginx 站点
- ✅ 安装 systemd 服务单元
- ✅ 配置 UFW 防火墙（SSH/HTTP/HTTPS）

**注意**：脚本会输出 PostgreSQL 数据库密码（若使用 PostgreSQL），**请保存好**。

### 第三步：配置环境变量

```bash
cp .env.example /opt/ai-girlfriend/.env
nano /opt/ai-girlfriend/.env
```

**必须修改的项：**

| 变量 | 说明 | 生成命令 |
|------|------|----------|
| `AI_GF_ENV` | 运行环境；生产必须设为 `prod`（非显式 dev 才启用 fail-closed 校验） | 直接设为 `prod` |
| `API_KEY` | X-API-Key（**禁止**使用 .env.example 占位符；非 dev 启用认证时 fail-closed） | `openssl rand -base64 32` |
| `JWT_SECRET` | JWT 签名密钥（**非显式 dev 必填**，≥32；缺失则 auth_jwt 拒绝启动） | `openssl rand -base64 48` |
| `API_CORS_ORIGINS` | 允许的前端域名 | 设为 `https://你的域名` |
| `DATABASE_URL` | 可选；留空默认 SQLite `data/users.db`，用 PostgreSQL 时填 `postgresql://...`（需安装 asyncpg） | — |

根据你使用的 LLM 供应商，至少配置一个 API Key（智谱/讯飞/百度/DeepSeek）。

### 第四步：配置 SSL 证书

```bash
# 先确保 DNS 已指向服务器 IP
sudo certbot --nginx -d unique-you.yourdomain.com
```

certbot 会自动修改 Nginx 配置并启用 HTTPS。
证书会自动续期（systemd timer），无需手动操作。

### 第五步：部署应用

#### 方式 A：本地 push + 服务器 pull（推荐）

> 服务器是 git 克隆且可访问 GitHub。`git archive` 覆盖式同步已废弃——它会静默吞掉服务器本地修改。

在本地开发机执行：

```bash
git push origin main
```

在服务器执行：

```bash
cd /opt/ai-girlfriend
git pull origin main
bash deploy/remote_deploy.sh
```

> ⚠️ **前提**：SSH 密钥已配置（`ssh-keygen` + `ssh-copy-id deploy@服务器IP`）。
> **SECURITY**：公开仓勿提交真实生产 IP/SSH 用户；若历史版本曾暴露主机地址或凭据，
> 请轮换凭据并复查该主机 SSH/nginx 暴露面。部署目标优先用 `DEPLOY_HOST`/`DEPLOY_DOMAIN` 注入。

#### 方式 B：在服务器上执行部署脚本

```bash
cd /opt/ai-girlfriend
sudo bash deploy/deploy.sh
```

> `deploy.sh` 会先 `git fetch origin` / `git checkout main` / `git pull origin main`，再安装依赖、构建前端并重启服务；服务器必须能访问 GitHub。

部署脚本完成：

1. `git fetch origin && git checkout main && git pull origin main`
2. `pip install -e .` 更新 Python 依赖
3. `npm install && npm run build` 构建前端
4. 复制前端文件到 Nginx 静态目录
5. 重启后端服务
6. 重载 Nginx

> 💡 **首次部署**：部署完成后，运行 python deploy/seed.py 一键生成所有角色的知识库索引（后续新增角色只需重新运行此命令即可增量添加）。

---

## 验证部署

```bash
# 检查后端服务状态
systemctl status ai-girlfriend

# 检查 API 是否响应
curl http://127.0.0.1:8000/health

# 通过域名访问前端
curl -I https://unique-you.yourdomain.com

# 测试 API 鉴权
curl -H "X-API-Key: your-api-key" https://unique-you.yourdomain.com/api/v1/status
```

---

## 监控与运维

### 查看日志

```bash
# 后端日志（实时）
journalctl -u ai-girlfriend -f

# Nginx 访问日志
tail -f /var/log/nginx/ai-girlfriend-access.log

# Nginx 错误日志
tail -f /var/log/nginx/ai-girlfriend-error.log

# 应用自定义日志
tail -f /opt/ai-girlfriend/logs/uvicorn.log
```

### 常用操作

```bash
# 重启后端
sudo systemctl restart ai-girlfriend

# 重启 Nginx
sudo systemctl reload nginx

# 查看后端资源占用
systemctl status ai-girlfriend
top -p $(pgrep -f uvicorn)

# 数据库备份（推荐使用自动备份脚本）
bash deploy/backup.sh

# 查看备份列表
ls -la /opt/ai-girlfriend/backups/
```

### 健康检查端点

| 端点 | 用途 |
|------|------|
| `/health` | 基础健康检查 |
| `/api/health` | API 健康（含数据库连接） |
| `/metrics` | Prometheus 指标（如果启用） |
| Sentry | 错误监控（配置 SENTRY_DSN 后自动启用） |

---

## 更新应用

### 从本地开发机（推荐）

```bash
# 本地
git push origin main

# 服务器
cd /opt/ai-girlfriend
git pull origin main
bash deploy/remote_deploy.sh
```

### 在服务器上

```bash
cd /opt/ai-girlfriend
sudo bash deploy/deploy.sh
```

> 注意：`deploy.sh` 会在服务器上执行 `git fetch origin` / `git checkout main` / `git pull origin main`；
> 服务器无法访问 GitHub 时该方式不可用，需先解决网络或走方式 A 的 push/pull 通道
> （`git archive` 覆盖式同步已废弃）。

如果你要回滚：

```bash
cd /opt/ai-girlfriend
git log --oneline -10        # 找到要回滚的 commit
git reset --hard <commit-hash>
sudo systemctl restart ai-girlfriend
```

---

## 架构概览

```
                          ┌──────────────┐
                          │   用户浏览器   │
                          └──────┬───────┘
                                 │ HTTPS
                          ┌──────▼───────┐
                          │    Nginx      │  :443
                          │  (反向代理)    │
                          └──┬───────┬───┘
                             │       │
                    ┌───────────────────┐ ┌──▼────────┐
                    │ 前端静态文件        │ │ /api/*     │
                    │ frontend/dist      │ │ /ws        │
                    └────────────────────┘ └──┬─────────┘
                                     │ HTTP
                            ┌────────▼────────┐
                            │  uvicorn x 4    │  :8000
                            │  FastAPI 后端    │
                            └────────┬────────┘
                                     │
                            ┌────────▼────────┐
                            │ SQLite（默认）   │
                            │ PostgreSQL（可选）│
                            └─────────────────┘
```

> 前端静态文件实际目录：`/opt/ai-girlfriend/frontend/dist`（Nginx `root`）；
> 数据库默认 SQLite `data/users.db`，可选 PostgreSQL（需安装 asyncpg）。

---

## 常见问题

### Q: 服务起不来，怎么办？

```bash
# 看 journalctl 的具体错误
journalctl -u ai-girlfriend --no-pager -n 50

# 常见原因：
# 1. .env 文件缺失或格式错误
# 2. 使用 PostgreSQL 时数据库未启动
# 3. 端口 8000 被占用
# 4. Python 依赖未安装

# 手动启动看错误
cd /opt/ai-girlfriend && sudo -u www-data bash -c '
source .venv/bin/activate
PYTHONPATH=. uvicorn api.run_api:app --host 127.0.0.1 --port 8000
'
```

### Q: 使用 PostgreSQL 时数据库连接失败？

> 默认 SQLite 部署可跳过本节。

```bash
# 确认 PostgreSQL 在运行
systemctl status postgresql

# 测试连接
sudo -u unique_you psql -h localhost -d unique_you

# 检查 pg_hba.conf
sudo cat /etc/postgresql/15/main/pg_hba.conf | grep local
```

### Q: 前端白屏 / 404？

```bash
# 确认前端文件已部署
ls -la /opt/ai-girlfriend/frontend/dist/

# 确认 Nginx 配置正确
nginx -t

# 检查 Nginx error log
tail -f /var/log/nginx/ai-girlfriend-error.log

# 刷新浏览器缓存（硬刷新：Ctrl+Shift+R）
```

### Q: SSL 证书问题？

```bash
# 检查证书状态
sudo certbot certificates

# 手动续期（测试）
sudo certbot renew --dry-run

# 强制重新申请
sudo certbot --nginx -d yourdomain.com --force-renewal
```

### Q: 如何迁移数据库？

```bash
# 使用备份脚本导出
bash deploy/backup.sh

# 或手动导出
pg_dump -U unique_you -h localhost unique_you > dump.sql

# 导入到新服务器
psql -U unique_you -h localhost unique_you < dump.sql
```

---

## 日志位置速查

| 日志 | 位置 |
|------|------|
| 后端 (journald) | `journalctl -u ai-girlfriend` |
| 后端 (uvicorn) | `/opt/ai-girlfriend/logs/uvicorn.log` |
| Nginx 访问日志 | `/var/log/nginx/ai-girlfriend-access.log` |
| Nginx 错误日志 | `/var/log/nginx/ai-girlfriend-error.log` |
| PostgreSQL 日志 | `/var/log/postgresql/postgresql-15-main.log` |

---

## 文件清单

```
deploy/
├── nginx-ai-girlfriend.conf          # Nginx 反向代理配置（含 ${DOMAIN} 占位符）
├── ai-girlfriend.service     # systemd 服务单元
├── deploy.sh                         # 部署脚本（拉取→构建→重启）
├── setup.sh                          # 初始化脚本（一次性的）
├── start.sh                          # 生产启动脚本（手动模式）
└── README.deploy.md                  # 本文件
```

> 环境变量模板：仓库根 `.env.example`（`deploy/` 下没有 `.env.production`）。

## 语音链路系统依赖（2026-11-08 补）

语音合成/发送不只靠 Python 包，宿主必须装：

```bash
# OpenCloudOS 9 / RHEL9 系（生产实测）
dnf install -y ffmpeg vulkan-loader      # ffmpeg 7 依赖 libvulkan.so.1
.venv-cpu/bin/pip install "pilk>=0.2.4"  # mp3→wav→silk（微信 tencent 变体）
```

缺 `ffmpeg` → `ffmpeg未找到`；缺 `vulkan-loader` → `libvulkan.so.1` 加载失败；
缺 `pilk` → 只能退 `amr`（多数 ffmpeg 构建不带 amr 编码器）→ 语音条发不出。
验证：`.venv-cpu/bin/python -c "from voice.audio_converter import AudioFormatConverter; print(AudioFormatConverter().health_check())"`。
