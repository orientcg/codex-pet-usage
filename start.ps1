param(
    [switch]$FollowCodex,
    [switch]$DisableFollowCodex
)

$ErrorActionPreference = 'Stop'
if ($FollowCodex -and $DisableFollowCodex) {
    throw 'Choose either -FollowCodex or -DisableFollowCodex.'
}
$petProjectRoot = $PSScriptRoot
$petCandidates = @(
    (Join-Path $petProjectRoot '.venv\Scripts\pythonw.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\pythonw.exe')
)
$petPython = $petCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $petPython) {
    $petPythonCommand = Get-Command pythonw.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($petPythonCommand) {
        $petPython = $petPythonCommand.Source
    }
}
if (-not $petPython) {
    $petShell = New-Object -ComObject WScript.Shell
    $null = $petShell.Popup('Python was not found. Follow README.md to create .venv and install requirements.txt.', 0, 'Codex Pet', 48)
    exit 1
}
if ($FollowCodex -or $DisableFollowCodex) {
    $petAutostartAction = if ($FollowCodex) { '--enable' } else { '--disable' }
    $petConfiguration = Start-Process -FilePath $petPython -ArgumentList ('"' + (Join-Path $petProjectRoot 'pet_autostart.py') + '" ' + $petAutostartAction) -WorkingDirectory $petProjectRoot -WindowStyle Hidden -PassThru
    $petConfiguration.WaitForExit()
    if ($petConfiguration.ExitCode -ne 0) {
        throw 'Follow Codex configuration failed. See work/autostart.log.'
    }
    exit 0
}
Start-Process -FilePath $petPython -ArgumentList ('"' + (Join-Path $petProjectRoot 'pet_usage.py') + '"') -WorkingDirectory $petProjectRoot -WindowStyle Hidden
