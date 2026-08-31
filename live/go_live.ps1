# forexbot GO-LIVE assembly script (run on VPS, C:\forexbot)
# Order: preflight -> stop writers -> backup .env -> split DB -> apply TG-owner patch -> restart -> verify.
# Terminal MT5 is NOT touched by this script (it must already be logged into PU Prime 29399422).
#
# PREREQUISITES (manual, BEFORE running):
#   1) Security: passwords rotated (PU Prime account 29399422, VPS Administrator,
#      RDP), Telegram token re-issued by @BotFather, GitHub token re-issued.
#   2) MT5 on VPS logged into the live account (File -> Login to trade account,
#      29399422 / PUPrime-Live, "Save password" checked). Demo stops here.
#   3) live\check_live.py passed: login 29399422, server PUPrime-Live,
#      balance ~150, trade_allowed True; HYBRID_SYMBOLS in .env updated from
#      its symbol table if live suffixes differ.
#   4) C:\forexbot\.env updated from live\env_live.template
#      (MT5_LOGIN / MT5_PASSWORD / TRADING_MODE=real / HYBRID_TEST_BALANCE=150 /
#      TELEGRAM_OWNER_ID / TELEGRAM_BOT_TOKEN).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File live\go_live.ps1           # with confirm prompt
#   powershell -ExecutionPolicy Bypass -File live\go_live.ps1 -Yes      # no prompt

param([switch]$Yes)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent     # C:\forexbot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

Write-Host '=== 0. PREFLIGHT ==='
if (-not (Test-Path $py)) { throw "python not found: $py" }

Write-Host '--- .env live checks ---'
$env_lines = Get-Content (Join-Path $root '.env') -ErrorAction Stop
function Env-Has($key) {
    foreach ($l in $env_lines) { if ($l -match "^\s*$key\s*=\s*(.+)$") { return $Matches[1].Trim() } }
    return $null
}
$must = @('MT5_LOGIN', 'MT5_SERVER', 'MT5_PASSWORD', 'TRADING_MODE', 'HYBRID_TEST_BALANCE', 'TELEGRAM_OWNER_ID', 'TELEGRAM_BOT_TOKEN')
$missing = @($must | Where-Object { -not (Env-Has $_) })
if ($missing.Count -gt 0) { throw ".env missing keys: $($missing -join ', ')" }
if ((Env-Has 'MT5_LOGIN') -ne '29399422')        { throw '.env MT5_LOGIN is not 29399422' }
if ((Env-Has 'MT5_SERVER') -ne 'PUPrime-Live')   { throw '.env MT5_SERVER is not PUPrime-Live' }
if ((Env-Has 'TRADING_MODE') -ne 'real')         { throw '.env TRADING_MODE is not real' }
if ((Env-Has 'MT5_PASSWORD') -match '^<')        { throw '.env MT5_PASSWORD still a placeholder' }
if ((Env-Has 'TELEGRAM_BOT_TOKEN') -match '^<')  { throw '.env TELEGRAM_BOT_TOKEN still a placeholder' }
Write-Host ("env ok: login={0} server={1} mode={2} base={3}" -f (Env-Has 'MT5_LOGIN'), (Env-Has 'MT5_SERVER'), (Env-Has 'TRADING_MODE'), (Env-Has 'HYBRID_TEST_BALANCE'))

if (-not $Yes) {
    $a = Read-Host 'This stops the bots, archives demo DB and applies TG-owner patch. Continue? (y/N)'
    if ($a -notmatch '^[yY]') { Write-Host 'aborted'; exit 1 }
}

Write-Host '=== 1. STOP WRITERS (hybrid / telegram / web; terminal NOT touched) ==='
foreach ($tn in 'fxbot-hybrid', 'fxbot-telegram', 'fxbot-web') {
    schtasks /End /TN $tn 2>&1 | Out-Null
    Write-Host "ended task $tn"
}
# Gotcha from CONTEXT: schtasks /Run alone does NOT restart webapp (stale PIDs).
# Kill leftover pythons so tasks start fresh. MT5 terminal64.exe is NOT killed.
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host 'leftover python processes killed'

Write-Host '=== 2. BACKUP .env ==='
New-Item -ItemType Directory -Force -Path (Join-Path $root 'data\archive') | Out-Null
Copy-Item (Join-Path $root '.env') (Join-Path $root "data\archive\env_before_live_$stamp") -Force
Write-Host "saved data\archive\env_before_live_$stamp"

Write-Host '=== 3. SPLIT DB (demo history -> archive, working DB -> clean) ==='
& $py (Join-Path $root 'live\split_db.py') --dry-run
if ($LASTEXITCODE -ne 0) { throw 'split_db dry-run failed' }
& $py (Join-Path $root 'live\split_db.py') --yes
if ($LASTEXITCODE -ne 0) { throw 'split_db failed' }

Write-Host '=== 4. APPLY TG-OWNER PATCH (/stop //start owner-only; /stop keeps terminal on real) ==='
& $py (Join-Path $root 'live\patch_tg_owner.py') --apply
if ($LASTEXITCODE -ne 0) { throw 'patch_tg_owner failed (restore from .bak_* if needed)' }

Write-Host '=== 5. RESTART TASKS ==='
foreach ($tn in 'fxbot-hybrid', 'fxbot-web', 'fxbot-telegram') {
    schtasks /Run /TN $tn | Out-Null
    Write-Host "started task $tn"
}

Write-Host '=== 6. VERIFY (20s warm-up) ==='
Start-Sleep -Seconds 20
try {
    $r = Invoke-WebRequest -UseBasicParsing http://localhost:8181/ -TimeoutSec 10
    Write-Host ("web status: " + $r.StatusCode)
} catch { Write-Host ("web err: " + $_.Exception.Message) }
foreach ($log in 'data\hybrid_boot.log', 'data\hybrid.log') {
    $p = Join-Path $root $log
    if (Test-Path $p) { Write-Host "--- $log (tail) ---"; Get-Content $p -Tail 12 }
}

Write-Host ''
Write-Host '=== DONE ==='
Write-Host 'Check now: site fxbot.space shows REAL account, /status shows mode REAL,'
Write-Host 'channel gets [LIVE] tags. Watch first 2-3 trades manually (lot, SL/TP, hybrid magic),'
Write-Host 'daily stop 8% = 12 USD. Then copier: keep COPIER_DRY_RUN=1 until trader account arrives.'
