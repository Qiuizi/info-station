<#
.SYNOPSIS
  信息监控站 · 一键开发启动（Windows PowerShell 5+/7）

.EXAMPLE
  .\dev.ps1            # 安装依赖（首次）→ 新窗口启动后端 → 启动 Tauri 桌面窗口
  .\dev.ps1 -Web       # 不开 Tauri，只开浏览器版前端（http://localhost:1420）
  .\dev.ps1 -Backend   # 只启动后端
  .\dev.ps1 -Install   # 只安装 / 更新依赖
#>
param(
    [switch]$Web,
    [switch]$Backend,
    [switch]$Install
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
# 让 Python 后端在 Windows 上始终用 UTF-8（避免 GBK 控制台/日志乱码）
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$Root = $PSScriptRoot
$BackendDir = Join-Path $Root "backend"
$DesktopDir = Join-Path $Root "apps\desktop"

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "✗ 没找到 $cmd。$hint" -ForegroundColor Red
        exit 1
    }
}

Write-Host "`n  信息监控站 · Info Station`n" -ForegroundColor Magenta
Need "uv" "安装：winget install --id=astral-sh.uv -e   （装完重开终端）"
Need "pnpm" "安装：先装 Node.js 22+（winget install OpenJS.NodeJS.LTS），再运行 corepack enable pnpm"
if (-not $Web -and -not $Backend) { Need "cargo" "安装 Rust：winget install Rustlang.Rustup，并按 README 安装 MSVC 生成工具" }

# ---------- 依赖
if ($Install -or -not (Test-Path (Join-Path $BackendDir ".venv"))) {
    Write-Host "→ 安装后端依赖（uv sync）" -ForegroundColor Cyan
    Push-Location $BackendDir; uv sync --extra browser; Pop-Location
}
if ($Install -or -not (Test-Path (Join-Path $DesktopDir "node_modules"))) {
    Write-Host "→ 安装前端依赖（pnpm install）" -ForegroundColor Cyan
    Push-Location $DesktopDir; pnpm install; Pop-Location
}
if ($Install) { Write-Host "✓ 依赖已就绪" -ForegroundColor Green; exit 0 }

if (-not $env:DEEPSEEK_API_KEY) {
    Write-Host "  提示：未检测到 DEEPSEEK_API_KEY，可在应用「设置」里填写；不填则只用规则打分。" -ForegroundColor DarkYellow
}

# ---------- 后端
$alive = $false
try { $alive = (Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2).ok } catch {}
if ($alive) {
    Write-Host "✓ 后端已在 8765 端口运行" -ForegroundColor Green
} elseif ($Backend) {
    Push-Location $BackendDir; uv run python run.py; Pop-Location; exit 0
} else {
    Write-Host "→ 在新窗口启动后端：http://127.0.0.1:8765" -ForegroundColor Cyan
    $cmd = "Set-Location '$BackendDir'; `$Host.UI.RawUI.WindowTitle='Info Station 后端'; uv run python run.py"
    $shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }
    Start-Process $shell -ArgumentList "-NoExit", "-Command", $cmd | Out-Null
}

# ---------- 前端
Push-Location $DesktopDir
try {
    if ($Web) {
        Write-Host "→ 浏览器版前端：http://localhost:1420" -ForegroundColor Cyan
        Start-Process "http://localhost:1420"
        pnpm dev
    } else {
        Write-Host "→ 启动 Tauri 桌面窗口（首次编译 Rust 需要几分钟）" -ForegroundColor Cyan
        pnpm tauri dev
    }
} finally { Pop-Location }
