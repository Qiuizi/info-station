<#
.SYNOPSIS
  用 PyInstaller 把后端打成 Tauri sidecar，并复制到 apps\desktop\src-tauri\binaries\
#>
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Windows PowerShell 5.1 里，$ErrorActionPreference = "Stop" 会把原生程序写到 stderr 的普通进度信息
# （uv / pnpm / cargo 都这样）当成终止错误，脚本在第一条命令就静默退出。原生命令一律用 Invoke-Native 调用：
# 临时放宽错误策略，只按退出码判断成败。
function Invoke-Native {
    param([Parameter(Mandatory)][string]$Exe, [Parameter(ValueFromRemainingArguments)][string[]]$ArgList)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Exe @ArgList 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($ArgList -join ' ') 失败（退出码 $LASTEXITCODE）" }
}
$Root = Split-Path $PSScriptRoot -Parent
$BackendDir = Join-Path $Root "backend"
$BinDir = Join-Path $Root "apps\desktop\src-tauri\binaries"

$triple = ((rustc -Vv | Select-String "^host:").ToString() -split "\s+")[1]
if (-not $triple) { throw "无法获取 Rust 目标三元组（rustc -Vv）" }
Write-Host "→ 目标三元组：$triple" -ForegroundColor Cyan

Push-Location $BackendDir
try {
    Invoke-Native uv sync --extra browser --extra build
    Invoke-Native uv run pyinstaller info-station-backend.spec --noconfirm --clean
} finally { Pop-Location }

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$target = Join-Path $BinDir "info-station-backend-$triple.exe"
Copy-Item (Join-Path $BackendDir "dist\info-station-backend.exe") $target -Force
Write-Host "✓ sidecar 已就绪：$target" -ForegroundColor Green
