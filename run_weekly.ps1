# Weekly chess-trainer pipeline: fetch new games, analyze, tag, write report.
# Registered as a Windows Scheduled Task (see SETUP.md) so this runs itself -
# the whole point of the MVP's "run for 4-6 weeks" phase is that it doesn't
# need anyone to remember to kick it off by hand.

$ErrorActionPreference = "Stop"
$ProjectDir = "G:\My Drive\Claude Code\Projects\chess-trainer"
$Python = "C:\Users\Tenxt\.venvs\chess-trainer\Scripts\python.exe"
$LogDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("run-{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))

Set-Location $ProjectDir
try {
    & $Python -m chess_trainer.cli run *>&1 | Tee-Object -FilePath $LogFile
    Write-Output "Weekly run completed successfully. Log: $LogFile"
} catch {
    Write-Output "Weekly run FAILED: $_"
    Add-Content -Path $LogFile -Value "FAILED: $_"
    exit 1
}
