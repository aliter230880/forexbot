# Forex Bot launcher: starts all bots + web panel, then opens admin page.
# ASCII-only on purpose: PowerShell reads BOM-less files as ANSI and breaks on Cyrillic.
$dir = "E:\AI\AI_folder\forexbot"
$py = "$dir\.venv\Scripts\python.exe"
$url = "http://localhost:8181/admin"

Write-Host ""
Write-Host "  === FOREX BOT - START ===" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $py)) {
  Write-Host "  ERROR: python not found: $py" -ForegroundColor Red
  Read-Host "  Press Enter to exit"
  exit 1
}

Write-Host "  [1/4] Stopping old processes..." -ForegroundColor Gray
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object {
    $_.CommandLine -like "*backend.main *" -or
    $_.CommandLine -like "*backend.main_multi*" -or
    $_.CommandLine -like "*backend.main_scalp3*" -or
    $_.CommandLine -like "*backend.main_hybrid*" -or
    $_.CommandLine -like "*backend.webapp*"
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

Write-Host "  [2/4] Starting bots..." -ForegroundColor Gray
Start-Process -FilePath $py -ArgumentList "-m","backend.main","run" -WorkingDirectory $dir -WindowStyle Hidden
Write-Host "        + scalper (profile from .env)" -ForegroundColor DarkGray
Start-Process -FilePath $py -ArgumentList "-m","backend.main_multi","run" -WorkingDirectory $dir -WindowStyle Hidden
Write-Host "        + multi bot (6 instruments)" -ForegroundColor DarkGray
Start-Process -FilePath $py -ArgumentList "-m","backend.main_hybrid","run" -WorkingDirectory $dir -WindowStyle Hidden
Write-Host "        + hybrid (grid limits + hard SL)" -ForegroundColor DarkGray
Start-Process -FilePath $py -ArgumentList "-m","backend.main_scalp3","run" -WorkingDirectory $dir -WindowStyle Hidden
Write-Host "        + scalper v3 (ML, experimental)" -ForegroundColor DarkGray
Start-Process -FilePath $py -ArgumentList "-m","backend.webapp" -WorkingDirectory $dir -WindowStyle Hidden
Write-Host "        + web panel :8181" -ForegroundColor DarkGray

Write-Host "  [3/4] Waiting for web panel..." -ForegroundColor Gray
$ok = $false
for ($i = 1; $i -le 20; $i++) {
  Start-Sleep -Seconds 2
  try {
    $r = Invoke-WebRequest -Uri "http://localhost:8181/" -TimeoutSec 3 -UseBasicParsing
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch { }
}

if ($ok) {
  Write-Host "  [4/4] Opening admin panel: $url" -ForegroundColor Green
  Start-Process $url
} else {
  Write-Host "  [4/4] Web panel did not respond in 40s." -ForegroundColor Yellow
  Write-Host "        Check log: $dir\data\bot.log" -ForegroundColor Yellow
}

Write-Host ""
$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like "*backend.*" })
Write-Host ("  Processes running: " + $running.Count) -ForegroundColor Cyan
Write-Host ""
Write-Host "  Admin login: dim230880" -ForegroundColor White
Write-Host "  Password:    see ADMIN_PASS in .env" -ForegroundColor White
Write-Host ""
Write-Host "  IMPORTANT: MT5 terminal must be running, and inside it:" -ForegroundColor Yellow
Write-Host "  Tools -> Options -> Expert Advisors -> Allow algorithmic trading" -ForegroundColor Yellow
Write-Host ""
Start-Sleep -Seconds 6
