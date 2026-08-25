# Запуск ботов как независимые процессы (переживают закрытие терминала)
$dir = "E:\AI\AI_folder\forexbot"
$py = "$dir\.venv\Scripts\python.exe"

# убить старые экземпляры (скальпер, мульти-бот, веб)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object {
    $_.CommandLine -like "*backend.main *" -or
    $_.CommandLine -like "*backend.main_multi*" -or
    $_.CommandLine -like "*backend.main_hybrid*" -or
    $_.CommandLine -like "*backend.webapp*"
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Sleep -Seconds 2

# основной бот (профиль из .env: SCALP / STANDARD / MICRO)
Start-Process -FilePath $py -ArgumentList "-m", "backend.main", "run" `
  -WorkingDirectory $dir -WindowStyle Hidden
Write-Output "bot started (detached)"

# мульти-символьный бот (без трансляций, статистика в админке)
Start-Process -FilePath $py -ArgumentList "-m", "backend.main_multi", "run" `
  -WorkingDirectory $dir -WindowStyle Hidden
Write-Output "multi bot started (detached)"

# гибрид: лимитки сетки + жёсткий SL (стратегия №3, без трансляций)
Start-Process -FilePath $py -ArgumentList "-m", "backend.main_hybrid", "run" `
  -WorkingDirectory $dir -WindowStyle Hidden
Write-Output "hybrid bot started (detached)"

# дашборд + админка
Start-Process -FilePath $py -ArgumentList "-m", "backend.webapp" `
  -WorkingDirectory $dir -WindowStyle Hidden
Write-Output "webapp started (detached)"
