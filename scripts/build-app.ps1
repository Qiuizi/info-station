<#
.SYNOPSIS
  一键打包 Windows 安装包：先打 sidecar，再 tauri build（输出 NSIS .exe 与 .msi）
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
& (Join-Path $PSScriptRoot "build-sidecar.ps1")
Push-Location (Join-Path $Root "apps\desktop")
try {
    Invoke-Native pnpm install --frozen-lockfile
    Invoke-Native pnpm tauri build --config src-tauri/tauri.bundle.conf.json
} finally { Pop-Location }
Write-Host "✓ 安装包在 apps\desktop\src-tauri\target\release\bundle\ 下（nsis / msi）" -ForegroundColor Green
