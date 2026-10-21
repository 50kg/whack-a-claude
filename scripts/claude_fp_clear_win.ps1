# Legacy compatibility entry point. No default mutation: explicit confirmation required.
param([switch]$Scan, [switch]$Apply, [switch]$Yes, [switch]$KeepHistory,
      [switch]$ForceQuit, [switch]$IgnoreRunning, [string]$Restore='', [string]$Json='')
if ($ForceQuit -or $IgnoreRunning -or $Json) {
    Write-Error 'Automatic process termination, running-process bypass and JSON reports are no longer supported. Close Claude and use Start.bat.'
    exit 2
}
if ($Restore) {
    & "$PSScriptRoot/launch.ps1" restore $Restore
} elseif ($Scan) {
    & "$PSScriptRoot/launch.ps1" identity --scan
} else {
    & "$PSScriptRoot/launch.ps1" identity
}
exit $LASTEXITCODE
