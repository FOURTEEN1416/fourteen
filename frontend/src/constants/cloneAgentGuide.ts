/**
 * 克隆好友 · 智能体任务书（用户一键复制后粘贴给任意 AI 智能体执行）。
 * 权威副本：docs/guides/微信克隆-智能体任务书.md —— 修改时两处同步。
 */
export const CLONE_AGENT_GUIDE = `# 任务书：导出微信聊天记录 JSON（交给 AI 智能体执行）

给任何 AI 编码智能体（OpenCode / Claude Code / Cline / Cursor 等）。
配套工具：https://github.com/FOURTEEN1416/wechat-decrypt

## 1. 你的任务（智能体请先读完再动手）
帮用户在本地电脑上，导出微信与某位指定好友的聊天记录，产出 JSON 文件，供上传到 unique-you 平台做说话风格克隆。
成功标准：得到可解析、≤50MB、仅含该好友会话的 JSON，并指导用户完成上传。

## 2. 硬性约束（违反即停）
1. 全程只在用户本地电脑操作——微信数据库解密必须在微信登录的这台机器上进行。
2. 终端必须是管理员权限（whoami /groups 含 S-1-16-12288）。
3. 微信必须正在运行，且登录的是要克隆的账号。没登录先让用户登录，不要猜。
4. 数据隐私：聊天记录敏感。除用户主动上传 unique-you 外不得外传；不粘贴聊天原文。

## 3. 执行路径（按顺序，每步有完成判据）
### 步骤 0：环境自检
Windows + 微信 4.x 已登录运行；Python ≥ 3.10；管理员终端。任一不满足→停下告知用户。

### 步骤 1：获取导出工具
git clone https://github.com/FOURTEEN1416/wechat-decrypt.git
cd wechat-decrypt
python -m venv .venv
.venv\\Scripts\\python.exe -m pip install -r requirements.txt

### 步骤 2：解密数据库
打开并严格遵循该仓库的 AGENTS.md §0a「冷启动决策树」（密钥提取/解密的判据与排错以它为准）：
python main.py decrypt        # 完成判据: "结果: X 成功, Y 失败, Z 跳过" 且 X>0

### 步骤 3：确定目标联系人
列出可导出会话让用户指认（工具仓库 USAGE.md §7 有搜索联系人方法），拿到 wxid。不确定就问用户，禁止猜测。

### 步骤 4：导出该好友的 JSON
python export_all_chats.py exported_chats --dry-run --users <wxid>   # 先预览
python export_all_chats.py exported_chats --users <wxid>             # 正式导出
可选: --start 2025-01-01 --end 2025-12-31 按日期切片
完成判据: exit 0 且 "成功=A ... 总消息=N" 且 A>0

### 步骤 5：产物校验
- JSON 可被 json.load 解析（UTF-8）；元素含 is_self / text / message_type 字段（平台原生兼容）。
- 单文件 ≤ 50MB，超限按日期范围分片导出。

### 步骤 6：上传到 unique-you
推荐：平台控制台 → 创建角色 → 克隆好友 → 填名称 → 上传该 JSON → 等待人设预览。
备选（管理员）：
curl -X POST "<服务器>/api/clone/upload?target=<名称>" -H "X-API-Key: <Key>" -F "file=@exported_chats/<文件>.json"

### 步骤 7：收尾
报告导出条数/时间范围/文件路径/上传结果。文件保留与否由用户决定。

## 4. 故障速查
- 密钥提取失败：管理员终端 + 微信运行中 + 登录目标账号；4.1.9+ 走 Config.Cipher（工具内置）
- 解密全失败：salt 不匹配 → 重跑密钥提取；见工具仓库 AGENTS.md §4
- 中文乱码：以 UTF-8 读取
- 超 50MB：--start/--end 按月切片
- 导出 0 条：核对 wxid；会话可能无文本消息

## 5. 不确定就问用户，不要猜
参数细节以 --help 与工具仓库 README/USAGE/AGENTS.md 为准，冲突时以工具仓库为准。
`
