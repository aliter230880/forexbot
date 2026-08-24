# Запуск бота как независимый процесс (переживает закрытие терминала)
$dir = "E:\AI\AI_folder\forexbot"
$py = "$dir\.venv\Scripts\python.exe"

# убить старые экземпляры (бот и веб)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*backend.main*" -or $_.CommandLine -like "*backend.webapp*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Process -FilePath $py -ArgumentList "-m", "backend.main", "run" `
  -WorkingDirectory $dir -WindowStyle Hidden
Write-Output "bot started (detached)"

# дашборд, если не поднят
$web = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*backend.webapp*" }
if (-not $web) {
  Start-Process -FilePath $py -ArgumentList "-m", "backend.webapp" `
    -WorkingDirectory $dir -WindowStyle Hidden
  Write-Output "webapp started (detached)"
}
