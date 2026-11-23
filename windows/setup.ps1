# Chạy MỘT LẦN: cài thư viện và đặt lịch cho robot trên máy bạn.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "1) Cài thư viện Python..."
python -m pip install -r "$root\robot\requirements.txt"

if (-not (Test-Path "$root\robot\.env")) {
  Copy-Item "$root\robot\.env.example" "$root\robot\.env"
  Write-Host "2) Đã tạo file robot\.env - hãy mở nó bằng Notepad và điền thông tin Supabase."
} else { Write-Host "2) File robot\.env đã có." }

Write-Host "3) Đặt lịch chạy..."
$bat = "$root\windows\run_scheduled.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
# 12:10 mỗi ngày quét 1 lần. Nếu lỗi (mất mạng...) thì Windows tự thử lại sau 1 giờ (tối đa 6 lần). Lỡ giờ vì tắt máy thì quét bù khi bật máy/đăng nhập.
$t1 = New-ScheduledTaskTrigger -Daily -At "12:10"
$t2 = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
  -RestartCount 6 -RestartInterval (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "CupcakeDaily" -Action $action -Trigger @($t1, $t2) -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Xong. Robot sẽ tự chạy khi máy bật (từ giờ đã cài trong trang quản trị)."
Write-Host "Thử ngay: bấm đúp windows\test_agy.bat, rồi windows\quet_ngay.bat"
