# Creates "Vital Sign" shortcuts -- in this folder and on the Desktop -- that
# open the control window with the Vital Sign icon. Run once after setup:
#
#   powershell.exe -ExecutionPolicy Bypass -File create_shortcut.ps1
#
# A .cmd file can't carry an icon; a shortcut can. The shortcut runs the
# venv's pythonw.exe directly, so no console window appears either.

$ErrorActionPreference = "Stop"

$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pythonw = Join-Path $Dir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Pythonw)) {
    throw "No virtual environment at $Pythonw -- do the Setup steps in README.md first."
}

$Shell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
foreach ($Path in @((Join-Path $Dir "Vital Sign.lnk"), (Join-Path $Desktop "Vital Sign.lnk"))) {
    $Link = $Shell.CreateShortcut($Path)
    $Link.TargetPath = $Pythonw
    $Link.Arguments = '"' + (Join-Path $Dir "gui.py") + '"'
    $Link.WorkingDirectory = $Dir
    $Link.IconLocation = (Join-Path $Dir "assets\icon\vitalsign.ico") + ",0"
    $Link.Description = "Vital Sign control window"
    $Link.Save()
    Write-Output "Created $Path"
}
