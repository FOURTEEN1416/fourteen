# ═══════════════════════════════════════════════════════════
# 唯一的你 — 一键部署脚本 (Windows → Linux Server)
# ═══════════════════════════════════════════════════════════
# 链路：git archive → scp 上传 → 远程执行 remote_deploy.sh
#
# 用法：
#   powershell -File deploy_ai_girlfriend.ps1
#   powershell -File deploy_ai_girlfriend.ps1 -ServerUser root
#   powershell -File deploy_ai_girlfriend.ps1 -SkipTests
#
# 前提：
#   - SSH 密钥已配置（ssh-keygen + ssh-copy-id）
#   - 服务器 /opt/ai-girlfriend 目录已初始化
#   - 服务器已安装 Python 3.10+ / Node.js 22 / Nginx
# ═══════════════════════════════════════════════════════════

param(
    [string]$Server = "139.199.199.174",
    [string]$ServerUser = "deploy",
    [string]$RemoteDir = "/opt/ai-girlfriend",
    [string]$ArchiveName = "deploy_payload.tar.gz",
    [switch]$SkipTests,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($msg) { Write-Host "[STEP] $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[ERR]  $msg" -ForegroundColor Red }

# ─────────────────────────────────────────────────────────────
# Step 0: 前置检查
# ─────────────────────────────────────────────────────────────
Write-Step "0/6 前置检查"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err "git 未安装"; exit 1
}
if (-not (Get-Command scp -ErrorAction SilentlyContinue)) {
    Write-Err "scp 未安装（需要 OpenSSH 客户端）"; exit 1
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Err "ssh 未安装（需要 OpenSSH 客户端）"; exit 1
}

# 检查未提交变更
$dirty = git status --porcelain
if ($dirty) {
    Write-Warn "工作树有未提交变更，将仅打包已提交的代码："
    $dirty | Select-Object -First 5 | ForEach-Object { Write-Host "  $_" }
}

Write-OK "前置检查通过"

# ─────────────────────────────────────────────────────────────
# Step 1: 运行测试（可选）
# ─────────────────────────────────────────────────────────────
if (-not $SkipTests) {
    Write-Step "1/6 运行测试套件"
    & "$ProjectRoot\.venv\Scripts\python.exe" -m pytest --tb=short -q
    if ($LASTEXITCODE -ne 0) {
        Write-Err "测试失败，部署中止（使用 -SkipTests 跳过）"
        exit 1
    }
    Write-OK "测试通过"
} else {
    Write-Warn "1/6 跳过测试（-SkipTests）"
}

# ─────────────────────────────────────────────────────────────
# Step 2: 创建 git archive payload
# ─────────────────────────────────────────────────────────────
Write-Step "2/6 创建代码包"

$ArchivePath = Join-Path $ProjectRoot $ArchiveName
# 排除 .venv / node_modules / __pycache__ / .git 本身
git -C $ProjectRoot archive --format=tar.gz -o $ArchiveName HEAD
if ($LASTEXITCODE -ne 0) {
    Write-Err "git archive 失败"; exit 1
}

$size = (Get-Item $ArchivePath).Length / 1MB
Write-OK ("代码包已创建: {0} ({1:N2} MB)" -f $ArchiveName, $size)

# ─────────────────────────────────────────────────────────────
# Step 3: 上传到服务器
# ─────────────────────────────────────────────────────────────
Write-Step "3/6 上传代码包到服务器"

$scpTarget = "${ServerUser}@${Server}:${RemoteDir}/"
scp -o ConnectTimeout=10 $ArchivePath $scpTarget
if ($LASTEXITCODE -ne 0) {
    Write-Err "scp 上传失败（检查 SSH 密钥配置）"; exit 1
}
Write-OK "代码包已上传"

# ─────────────────────────────────────────────────────────────
# Step 4: 远程解压并安装依赖
# ─────────────────────────────────────────────────────────────
Write-Step "4/6 远程解压并安装依赖"

$remoteCmd = @"
set -euo pipefail
cd ${RemoteDir}
# 解压（覆盖现有文件）
tar xzf ${ArchiveName}
# 清理 payload
rm -f ${ArchiveName}
# 剥离 Windows CRLF（shell 脚本必须）
find deploy/ -name '*.sh' -exec sed -i 's/\r\$//' {} \;
# 执行远程部署
bash deploy/remote_deploy.sh
"@

ssh -o ConnectTimeout=10 ${ServerUser}@${Server} $remoteCmd
if ($LASTEXITCODE -ne 0) {
    Write-Err "远程部署失败"; exit 1
}
Write-OK "远程部署完成"

# ─────────────────────────────────────────────────────────────
# Step 5: 验证服务状态
# ─────────────────────────────────────────────────────────────
Write-Step "5/6 验证服务状态"

$verifyCmd = @"
systemctl is-active ai-girlfriend && echo SERVICE_ACTIVE || echo SERVICE_INACTIVE
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null || echo 'HEALTH_CHECK_FAILED'
"@

$result = ssh -o ConnectTimeout=10 ${ServerUser}@${Server} $verifyCmd
Write-Host "  服务状态: $($result -split "`n" | Select-Object -First 1)"
Write-Host "  健康检查: $($result -split "`n" | Select-Object -Skip 1 -First 1)"

# ─────────────────────────────────────────────────────────────
# Step 6: 清理本地 payload
# ─────────────────────────────────────────────────────────────
Write-Step "6/6 清理本地临时文件"

Remove-Item $ArchivePath -Force -ErrorAction SilentlyContinue
Write-OK "清理完成"

# ─────────────────────────────────────────────────────────────
# 完成
# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-OK "═══ 部署完成 ═══"
Write-Host "  服务器: http://$Server"
Write-Host "  前端:   http://$Server/"
Write-Host "  API:    http://$Server/api/"
Write-Host "  健康检查: http://$Server/health"
Write-Host ""
Write-Host "  查看日志: ssh ${ServerUser}@${Server} 'journalctl -u ai-girlfriend -f'"
