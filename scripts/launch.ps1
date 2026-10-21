$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $root 'whack_a_claude.py'
$native = Join-Path $root 'whack-a-claude.exe'
try {
    if (Test-Path -LiteralPath $native) {
        & $native @args
        exit $LASTEXITCODE
    }
    foreach ($candidate in @('py', 'python', 'python3')) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $prefix = @()
        if ($candidate -eq 'py') { $prefix = @('-3') }
        $valid = $false
        try {
            & $command.Source @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            $valid = $LASTEXITCODE -eq 0
        } catch { $valid = $false }
        if ($valid) {
            & $command.Source @prefix $entry @args
            exit $LASTEXITCODE
        }
    }
    Write-Host 'Python 3.10+ was not found. Install Python from https://www.python.org/downloads/'
    Write-Host 'Or download the Windows standalone ZIP from the GitHub Releases page.'
    exit 1
} catch {
    Write-Host ('Launch failed: ' + $_.Exception.Message)
    exit 1
}
