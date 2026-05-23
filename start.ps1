<#
.SYNOPSIS
    十四 AI 虚拟伴侣 — 一键启动脚本（后端API + 前端管理UI）
.DESCRIPTION
    启动后端 API 服务 (python main.py) + 前端开发服务器 (Vite)。
    所有操作通过管理 UI 完成，无需额外配置。
.NOTES
    使用 Ctrl+C 停止所有服务。
    微信、主动消息等高级功能默认不启动。
#>

$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $rootDir

# ── 颜色输出（避免 emoji，GBK 兼容）──
function Write-Info  { Write-Host "[INFO] $args" -ForegroundColor Cyan }
function Write-Ok    { Write-Host "[OK] $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "[WARN] $args" -ForegroundColor Yellow }
function Write-Error { Write-Host "[ERROR] $args" -ForegroundColor Red }

Write-Host ""
Write-Host "================================================" -ForegroundColor DarkCyan
Write-Host "  十四 - AI 虚拟伴侣 管理控制台一键启动"           -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor DarkCyan
Write-Host ""

# ── 1. 检查 Python ──
try {
    $pyVersion = python --version 2>&1
    Write-Ok "Python: $pyVersion"
} catch {
    Write-Error "未找到 Python，请安装 Python 3.10+"
    exit 1
}

# ── 2. 检查 Node.js ──
try {
    $nodeVersion = node --version 2>&1
    Write-Ok "Node.js: $nodeVersion"
} catch {
    Write-Error "未找到 Node.js，请安装 Node.js 18+"
    exit 1
}

# ── 3. 检查依赖 ──
Write-Info "检查 Python 依赖..."
try {
    python -c "import uvicorn, fastapi" 2>$null
    Write-Ok "Python 依赖已安装"
} catch {
    Write-Warn "缺少 Python 依赖，正在安装..."
    python -m pip install -r requirements.txt 2>$null
    if ($LASTEXITCODE -ne 0) {
        python -m pip install uvicorn fastapi 2>$null
    }
}

Write-Info "检查前端依赖..."
if (-not (Test-Path "frontend\node_modules")) {
    Write-Warn "缺少前端依赖，正在安装..."
    Push-Location frontend
    npm install 2>&1 | Out-Null
    Pop-Location
    Write-Ok "前端依赖安装完成"
} else {
    Write-Ok "前端依赖已安装"
}

# ── 4. 找到 vite ──
$vitePath = $null
$viteCandidate = Join-Path $rootDir "frontend\node_modules\.bin\vite"
if (Test-Path $viteCandidate) {
    $vitePath = $viteCandidate
} else {
    $vitePath = "npx"
}

# ── 5. 启动服务 ──
$backendJob = $null
$frontendJob = $null

try {
    # ── 后端 API ──
    Write-Info "启动后端 API 服务 (端口 8000)..."
    $env:PYTHONIOENCODING = "utf-8"
    $backendJob = Start-Job -Name "xiaonuan-api" -ScriptBlock {
        param($dir)
        $env:PYTHONIOENCODING = "utf-8"
        Set-Location $dir
        python main.py --no-wechat --no-scheduler
    } -ArgumentList $rootDir

    Start-Sleep -Seconds 5

    # ── 前端 Dev Server ──
    Write-Info "启动前端管理 UI (端口 5173)..."
    $frontendJob = Start-Job -Name "xiaonuan-frontend" -ScriptBlock {
        param($dir, $vite)
        Set-Location "$dir\frontend"
        if ($vite -ne "npx") {
            cmd /c "$vite --host"
        } else {
            npx vite --host
        }
    } -ArgumentList $rootDir, $vitePath

    Start-Sleep -Seconds 5

    # ── 检查启动状态 ──
    $backendRunning = ($backendJob.State -eq 'Running')
    $frontendRunning = ($frontendJob.State -eq 'Running')

    Write-Host ""
    Write-Host "================================================" -ForegroundColor DarkCyan
    if ($backendRunning -or $frontendRunning) {
        Write-Host "  启动完成！" -ForegroundColor Cyan
    }
    if ($backendRunning) {
        Write-Host "  后端 API: http://localhost:8000" -ForegroundColor Green
        Write-Host "  文档:     http://localhost:8000/docs" -ForegroundColor Green
    } else {
        Write-Host "  后端 API: 启动失败" -ForegroundColor Red
    }
    if ($frontendRunning) {
        Write-Host "  管理 UI: http://localhost:5173" -ForegroundColor Magenta
    } else {
        Write-Host "  管理 UI: 启动失败" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Ctrl+C 停止所有服务" -ForegroundColor DarkCyan
    Write-Host "================================================" -ForegroundColor DarkCyan
    Write-Host ""

    # ── 监控循环（含自动重启）──
    $backendRestartCount = 0
    $maxRestarts = 5
    while ($true) {
        $statusChanged = $false

        if ($backendJob.State -eq 'Completed' -or $backendJob.State -eq 'Failed') {
            if ($backendRunning) {
                $backendRunning = $false
                $statusChanged = $true
                $backendRestartCount++
                Write-Error "后端服务异常退出 (重启 $backendRestartCount/$maxRestarts)"
                $output = Receive-Job -Job $backendJob -ErrorAction SilentlyContinue
                if ($output) { Write-Host $output -ForegroundColor DarkGray }
            }
        }

        if ($frontendJob.State -eq 'Completed' -or $frontendJob.State -eq 'Failed') {
            if ($frontendRunning) {
                $frontendRunning = $false
                $statusChanged = $true
                Write-Warn "前端服务已停止"
            }
        }

        if ($statusChanged) {
            $apiStatus = if ($backendRunning) { "运行中" } else { "已停止" }
            $uiStatus = if ($frontendRunning) { "运行中" } else { "已停止" }
            Write-Host "[STATUS] API=$apiStatus | UI=$uiStatus" -ForegroundColor Gray
        }

        # 自动重启后端（最多 5 次）
        if (-not $backendRunning -and $backendRestartCount -le $maxRestarts) {
            Write-Warn "正在重启后端..."
            $env:PYTHONIOENCODING = "utf-8"
            $backendJob = Start-Job -Name "xiaonuan-api" -ScriptBlock {
                param($dir)
                $env:PYTHONIOENCODING = "utf-8"
                Set-Location $dir
                python main.py --no-wechat --no-scheduler
            } -ArgumentList $rootDir
            Start-Sleep -Seconds 3
            $backendRunning = ($backendJob.State -eq 'Running')
            if ($backendRunning) {
                Write-Ok "后端已重启"
            }
        }

        Start-Sleep -Seconds 5
    }
}
finally {
    # ── 清理 ──
    Write-Host ""
    Write-Info "正在停止服务..."

    if ($backendJob -and $backendJob.State -eq 'Running') {
        Stop-Job -Job $backendJob -ErrorAction SilentlyContinue
        Receive-Job -Job $backendJob -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $backendJob -ErrorAction SilentlyContinue
        Write-Ok "后端 API 已停止"
    }

    if ($frontendJob -and $frontendJob.State -eq 'Running') {
        Stop-Job -Job $frontendJob -ErrorAction SilentlyContinue
        Receive-Job -Job $frontendJob -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $frontendJob -ErrorAction SilentlyContinue
        Write-Ok "前端 UI 已停止"
    }

    Write-Host ""
    Write-Host "下次再来找我哦~" -ForegroundColor Cyan
    Write-Host ""
}
