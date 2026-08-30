#Requires -Version 7
<#
.SYNOPSIS
    ai-girlfriend 多窗口建窗（适配自 psd-framework §4 并行纪律，2026-08-28 入宪）。

.DESCRIPTION
    1. git worktree add ..\ai-girlfriend-<Name> -b wt/<Name>
    2. data/ Junction 回主仓（gitignore 生成物；SQLite/知识索引共享）
    3. frontend/node_modules Junction 回主仓（依赖不复制；Vite 缓存冲突时删 .vite）
    4. .venv 不复制——统一主仓解释器 D:\Desktop\ai-girlfriend\.venv（或系统 python）
    5. 打印窗口启动提示词

    卸窗：-Remove（先摘 Junction 再删树；禁止裸 git worktree remove——会跟随 Junction 误删主仓内容）

.PARAMETER Name
    窗口名（如 w1-byok）。分支 wt/<Name>，目录 ..\ai-girlfriend-<Name>。
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Name,
    [switch]$Remove,
    [switch]$ForceBranch
)

$ErrorActionPreference = "Stop"
$Main = "D:\Desktop\ai-girlfriend"
$Wt = Join-Path (Split-Path $Main) "ai-girlfriend-$Name"
$Branch = "wt/$Name"

function Remove-Junction([string]$link) {
    if (Test-Path $link) {
        cmd /c rmdir "$link" | Out-Null   # rmdir 只摘 Junction 不删目标
    }
}

if ($Remove) {
    if (-not (Test-Path $Wt)) { Write-Host "窗口不存在: $Wt"; exit 1 }
    Remove-Junction (Join-Path $Wt "data")
    Remove-Junction (Join-Path $Wt "frontend\node_modules")
    Set-Location $Main
    git worktree remove $Wt --force
    git worktree prune
    $merged = git branch --merged HEAD | Select-String $Branch
    if ($merged -or $ForceBranch) {
        git branch -D $Branch 2>$null
    } else {
        Write-Host "分支 $Branch 未并入 HEAD，保留（收编用 git merge --no-ff $Branch）"
    }
    Write-Host "✅ 窗口 $Name 已卸载"
    exit 0
}

if (Test-Path $Wt) { Write-Host "目录已存在: $Wt"; exit 1 }
Set-Location $Main
git worktree add $Wt -b $Branch

# data/ Junction（生成物共享：users.db / knowledge / important_dates 等）
New-Item -ItemType Junction -Path (Join-Path $Wt "data") -Target (Join-Path $Main "data") | Out-Null
# frontend/node_modules Junction
New-Item -ItemType Junction -Path (Join-Path $Wt "frontend\node_modules") -Target (Join-Path $Main "frontend\node_modules") | Out-Null

Write-Host @"
✅ 窗口 $Name 就绪：$Wt（分支 $Branch）

【启动提示词】（粘贴给该窗口的 agent）：
你是 ai-girlfriend 多窗口协作的窗口 '$Name'。工作目录 $Wt。
1. 先读 docs/board/BOARD.md（跨窗看板）再读你的任务包；
2. 一切读写只在本 worktree 内；提交用白名单精确 git add（禁 git add .），Conventional Commits 中文；
3. data/ 与 frontend/node_modules 是主仓 Junction，生成物带窗口前缀；
4. 完成后：窗口内回归（pytest -q + cd frontend && npx vitest run）→ 通知协调者收编
   （协调者执行：git merge --no-ff wt/$Name，收编前跑主检出回归门）。
"@