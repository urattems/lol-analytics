param([string]$DesktopPath = [Environment]::GetFolderPath('DesktopDirectory'))
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $PSHOME 'powershell.exe'
$arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'windows_bootstrap.ps1') + '" -Mode Launch'
if (-not (Test-Path -LiteralPath $DesktopPath -PathType Container)) { throw 'Desktop unavailable' }
$shell = New-Object -ComObject WScript.Shell
for ($index = 1; $index -le 100; $index++) {
    $name = if ($index -eq 1) { 'LoL Analytics.lnk' } else { "LoL Analytics ($index).lnk" }
    $destination = Join-Path $DesktopPath $name
    if (Test-Path -LiteralPath $destination) {
        $existing = $shell.CreateShortcut($destination)
        if ($existing.TargetPath -eq $target -and $existing.Arguments -eq $arguments) {
            Write-Output $destination
            exit 0
        }
        continue # Never overwrite a shortcut to another project/application.
    }
    $shortcut = $shell.CreateShortcut($destination)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = 'Ouvrir LoL Analytics - bibliotheque locale'
    $shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',14'
    $shortcut.Save()
    Write-Output $destination
    exit 0
}
throw 'No available shortcut name'
