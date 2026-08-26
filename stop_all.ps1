# Forex Bot stop button: kills all bots and the web panel.
# Pending orders and SL/TP stay on the broker server - they still work without the bot.
# ASCII-only on purpose: PowerShell reads BOM-less files as ANSI and breaks on Cyrillic.
Write-Host ""
Write-Host "  === FOREX BOT - STOP ===" -ForegroundColor Cyan
Write-Host ""

$found = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object {
    $_.CommandLine -like "*backend.main *" -or
    $_.CommandLine -like "*backend.main_multi*" -or
    $_.CommandLine -like "*backend.main_hybrid*" -or
    $_.CommandLine -like "*backend.webapp*"
  })

if ($found.Count -eq 0) {
  Write-Host "  Bots are not running." -ForegroundColor Gray
} else {
  foreach ($p in $found) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Write-Host ("  Stopped processes: " + $found.Count) -ForegroundColor Green
}

Write-Host ""
Write-Host "  Pending orders and stops remain on the broker server." -ForegroundColor Yellow
Write-Host "  To also close open positions, press 'Stop' for the bot" -ForegroundColor Yellow
Write-Host "  in the admin panel BEFORE stopping the processes." -ForegroundColor Yellow
Write-Host ""
Start-Sleep -Seconds 5
