#Requires -Version 7
<# 跨窗看板工具（适配 psd §4.8）。用法：
   pwsh scripts/window_board.ps1 -Append -Window w1-byok -Kind handoff  -Text "..."
   pwsh scripts/window_board.ps1 -Tail 5
#>
param(
    [switch]$Append,
    [switch]$Tail,
    [string]$Window = "",
    [string]$Kind = "note",
    [string]$Text = "",
    [int]$Count = 10
)
$Board = "D:\Desktopi-girlfriend\docsoard\BOARD.md"
if ($Tail) {
    Get-Content $Board -Tail $Count
    exit 0
}
if ($Append) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm"
    $line = "| $ts | $Window | $Kind | $Text |"
    Add-Content -Path $Board -Value $line -Encoding utf8
    Write-Host "appended: $line"
}
