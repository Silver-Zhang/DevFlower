$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = (Resolve-Path (Join-Path $Here '..\..')).Path
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { throw 'Python 3 was not found in PATH.' }
$Entry = Join-Path $Repo 'tools\devflower_launch.py'
$Shell = New-Object -ComObject WScript.Shell
$Targets = @(
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\DevFlower.lnk'),
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'DevFlower.lnk')
)
foreach ($Target in $Targets) {
  $Shortcut = $Shell.CreateShortcut($Target)
  $Shortcut.TargetPath = $Python.Source
  $Shortcut.Arguments = '-B "' + $Entry + '"'
  $Shortcut.WorkingDirectory = $Repo
  $Shortcut.Description = 'DevFlower Development workbench'
  $Shortcut.Save()
  Write-Output ("Installed: " + $Target)
}
Write-Output 'Uninstall: remove DevFlower.lnk from the desktop and Start Menu Programs.'
