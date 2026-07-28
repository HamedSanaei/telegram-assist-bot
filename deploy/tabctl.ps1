param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
$ErrorActionPreference = "Stop"
$Manager = Join-Path $PSScriptRoot "tabctl.py"
$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    & $Python.Source -3 $Manager @Arguments
} else {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) {
        $Candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            $Python = Get-Item -LiteralPath $Candidate
        }
    }
    if (-not $Python) {
        throw "Python 3.12 or newer is required to run tabctl."
    }
    & $Python.Source $Manager @Arguments
}
exit $LASTEXITCODE
