# 唯一的你 — 生产部署指南

> 预计阅读时间：10 分钟 · 部署时间：15-30 分钟

## 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Ubuntu | 22.04+ | 推荐 24.04 LTS |
| Python | 3.10 - 3.12 | 3.12 推荐 |
| PostgreSQL | 15+ | 生产必须 |
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
sudo mkdir -p /opt/unique-you
sudo chown $USER:$USER /opt/unique-you
git clone https://github.com/FOURTEEN1416/unique-you.git /opt/unique-you
cd /opt/unique-you
```

### 第二步：运行初始化脚本

```bash
sudo bash deploy/setup.sh
```

脚本自动完成：

- ✅ 安装系统依赖（Python, Nginx, PostgreSQL, Node.js, certbot）
- ✅ 创建虚拟环境并安装 Python 依赖
- ✅ 创建 PostgreSQL 数据库和用户
- ✅ 配置 Nginx 站点
- ✅ 安装 systemd 服务单元
- ✅ 配置 UFW 防火墙（SSH/HTTP/HTTPS）

**注意**：脚本会输出数据库密码，**请保存好**。

### 第三步：配置环境变量

```bash
cp deploy/.env.production /opt/unique-you/.env
nano /opt/unique-you/.env
```

**必须修改的项：**

| 变量 | 说明 | 生成命令 |
|------|------|----------|
| `API_KEY` | API 鉴权密钥 | `openssl rand -base64 48` |
| `JWT_SECRET` | JWT 签名密钥 | `openssl rand -base64 48` |
| `API_CORS_ORIGINS` | 允许的前端域名 | 设为 `https://你的域名` |
| `DATABASE_URL` | 数据库连接串 | 从 setup.sh 输出获取 |

根据你使用的 LLM 供应商，至少配置一个 API Key（智谱/讯飞/百度/DeepSeek）。

### 第四步：配置 SSL 证书

```bash
# 先确保 DNS 已指向服务器 IP
sudo certbot --nginx -d unique-you.yourdomain.com
```

certbot 会自动修改 Nginx 配置并启用 HTTPS。
证书会自动续期（systemd timer），无需手动操作。

### 第五步：部署应用

```bash
cd /opt/unique-you
sudo bash deploy/deploy.sh
```

部署脚本完成：

1. `git pull` 拉取最新代码
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
systemctl status unique-you-backend

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
journalctl -u unique-you-backend -f

# Nginx 访问日志
tail -f /var/log/nginx/unique-you-access.log

# Nginx 错误日志
tail -f /var/log/nginx/unique-you-error.log

# 应用自定义日志
tail -f /opt/unique-you/logs/uvicorn.log
```

### 常用操作

```bash
# 重启后端
sudo systemctl restart unique-you-backend

# 重启 Nginx
sudo systemctl reload nginx

# 查看后端资源占用
systemctl status unique-you-backend
top -p $(pgrep -f uvicorn)

# 数据库备份（推荐使用自动备份脚本）
bash deploy/backup.sh

# 查看备份列表
ls -la /opt/unique-you/backups/
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

```bash
cd /opt/unique-you
sudo bash deploy/deploy.sh
```

就这么简单。脚本会自动拉取、构建、重启。

如果你要回滚：

```bash
cd /opt/unique-you
git log --oneline -10        # 找到要回滚的 commit
git reset --hard <commit-hash>
sudo systemctl restart unique-you-backend
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
                    ┌────────▼──┐ ┌──▼────────┐
                    │ 前端静态文件 │ │ /api/*     │
                    │ /var/www/  │ │ /ws        │
                    └───────────┘ └──┬─────────┘
                                     │ HTTP
                            ┌────────▼────────┐
                            │  uvicorn x 4    │  :8000
                            │  FastAPI 后端    │
                            └────────┬────────┘
                                     │
                            ┌────────▼────────┐
                            │   PostgreSQL    │  :5432
                            └─────────────────┘
```

---

## 常见问题

### Q: 服务起不来，怎么办？

```bash
# 看 journalctl 的具体错误
journalctl -u unique-you-backend --no-pager -n 50

# 常见原因：
# 1. .env 文件缺失或格式错误
# 2. PostgreSQL 未启动
# 3. 端口 8000 被占用
# 4. Python 依赖未安装

# 手动启动看错误
cd /opt/unique-you && sudo -u www-data bash -c '
source .venv/bin/activate
PYTHONPATH=. uvicorn api.run_api:app --host 127.0.0.1 --port 8000
'
```

### Q: 数据库连接失败？

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
ls -la /var/www/unique-you/

# 确认 Nginx 配置正确
nginx -t

# 检查 Nginx error log
tail -f /var/log/nginx/unique-you-error.log

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
| 后端 (journald) | `journalctl -u unique-you-backend` |
| 后端 (uvicorn) | `/opt/unique-you/logs/uvicorn.log` |
| Nginx 访问日志 | `/var/log/nginx/unique-you-access.log` |
| Nginx 错误日志 | `/var/log/nginx/unique-you-error.log` |
| PostgreSQL 日志 | `/var/log/postgresql/postgresql-15-main.log` |

---

## 文件清单

```
deploy/
├── nginx.conf                        # Nginx 反向代理配置
├── unique-you-backend.service     # systemd 服务单元
├── deploy.sh                         # 部署脚本（拉取→构建→重启）
├── setup.sh                          # 初始化脚本（一次性的）
├── start.sh                          # 生产启动脚本（手动模式）
├── .env.production                   # 生产环境变量模板
└── README.deploy.md                  # 本文件
```
