# setup_task.ps1
# Ejecutar como Administrador para registrar/actualizar el bot como tarea de Windows.
# Configuracion robusta: corre en bateria y se auto-recupera tras suspension o apagado.

$botPath = "C:\Users\jccif\OneDrive\Documentos\Code\projects\trading-bot\start_bot.bat"
$taskName = "TradingBot"

$action = New-ScheduledTaskAction -Execute $botPath

# --- Triggers (multiples para maxima cobertura) ---

# 1. Al iniciar Windows
$triggerStartup = New-ScheduledTaskTrigger -AtStartup
$triggerStartup.Delay = "PT1M"

# 2. Al iniciar sesion el usuario (cubre despertar de suspension + login)
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn

# 3. Auto-recuperacion: cada 10 minutos, indefinidamente.
#    Si el bot se detuvo (suspension, bateria, crash), esto lo reinicia.
#    Si ya esta corriendo, MultipleInstances IgnoreNew evita duplicados.
$triggerRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# --- Settings: clave para que funcione en laptop ---
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger @($triggerStartup, $triggerLogon, $triggerRepeat) `
    -Settings $settings `
    -Principal $principal `
    -Force

Write-Host ""
Write-Host "Tarea '$taskName' actualizada con configuracion robusta." -ForegroundColor Green
Write-Host "  - Corre en bateria" -ForegroundColor Green
Write-Host "  - No se detiene al pasar a bateria" -ForegroundColor Green
Write-Host "  - Se auto-reinicia cada 10 min si se detiene" -ForegroundColor Green
Write-Host "  - Arranca al iniciar Windows y al iniciar sesion" -ForegroundColor Green
Write-Host ""

# Arrancar de inmediato
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8
$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "Estado actual: $state" -ForegroundColor Cyan
