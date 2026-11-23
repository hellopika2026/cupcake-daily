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
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -Command `"& '$bat'`"" -WorkingDirectory $root
# Từ 11:00 mỗi 15 phút trong 7 tiếng: robot tự kiểm tra giờ cài đặt và chỉ chạy đúng 1 lần mỗi ngày
$t1 = New-ScheduledTaskTrigger -Daily -At "11:00"
$rep = (New-ScheduledTaskTrigger -Once -At "11:00" -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 7)).Repetition
$t1.Repetition = $rep
$t2 = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "CupcakeDaily" -Action $action -Trigger @($t1, $t2) -Settings $settings -Force | Out-Null
Write-Host "Xong. Robot sẽ tự chạy khi máy bật (từ giờ đã cài trong trang quản trị)."
Write-Host "Thử ngay: bấm đúp windows\test_agy.bat, rồi windows\quet_ngay.bat"
