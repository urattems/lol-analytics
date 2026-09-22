param(
    [ValidateSet('Configure', 'Launch', 'Demo')][string]$Mode = 'Configure',
    [switch]$PrepareOnly
)
# ASCII intentionally: Windows PowerShell 5.1 reads UTF-8 without BOM as ANSI.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Find-Python {
    # A missing py -3.x writes stderr; it is a candidate miss, not fatal.
    $ErrorActionPreference = 'Continue'
    # Prefer 3.13 for new installations; retain an existing compatible venv.
    $probe = 'import sys, tkinter, venv; assert (3,11) <= sys.version_info[:2] < (3,14); assert sys.maxsize > 2**32; print(sys.executable)'
    foreach ($version in @('3.13', '3.12', '3.11')) {
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            $found = & py.exe "-$version" -c $probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $found) { return [string]($found | Select-Object -Last 1) }
        }
        $digits = $version.Replace('.', '')
        $candidate = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) "Programs\Python\Python$digits\python.exe"
        if (Test-Path -LiteralPath $candidate) {
            $found = & $candidate -c $probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $found) { return [string]($found | Select-Object -Last 1) }
        }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    # Do not trigger the Microsoft Store placeholder when Python is absent.
    if ($command -and $command.Source -notmatch '\\WindowsApps\\python.exe$') {
        $found = & $command.Source -c $probe 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return [string]($found | Select-Object -Last 1) }
    }
    return $null
}

try {
    Write-Host ''
    Write-Host '  LOL ANALYTICS - Bienvenue !' -ForegroundColor Cyan
    Write-Host '  La preparation est automatique. Rien a taper dans cette fenetre.'
    Write-Host '  Internet est necessaire pour la premiere installation.'
    Write-Host ''
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'app.py'))) {
        throw 'Extrayez TOUT le ZIP dans un dossier avant de lancer INSTALLER.bat.'
    }
    $venvRoot = Join-Path $projectRoot '.venv'
    $pythonExe = Join-Path $venvRoot 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        if (Test-Path -LiteralPath $venvRoot) {
            throw 'Le dossier .venv existe mais Python est absent. Il est conserve. Renommez ce dossier en venv-ancien puis relancez INSTALLER.bat.'
        }
        $basePython = Find-Python
        if (-not $basePython) {
            $answer = [System.Windows.Forms.MessageBox]::Show(
                "Python 64 bits (3.11 a 3.13) avec Tcl/Tk est necessaire.`n`nInstaller Python 3.13 pour votre compte Windows avec WinGet ?`nLe paquet Python.Python.3.13 utilise l'installateur officiel de python.org. Une connexion Internet est necessaire.`n`nAucune installation administrateur ni modification de vos donnees LoL.",
                'LoL Analytics - Installer Python', 'YesNo', 'Question')
            if ($answer -ne 'Yes') { exit 0 }
            if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
                [System.Windows.Forms.MessageBox]::Show(
                    "WinGet n'est pas disponible. La page officielle Python va s'ouvrir.`nInstallez Python 3.13 (Windows installer 64-bit), conservez Tcl/Tk, puis relancez INSTALLER.bat.",
                    'Installation manuelle de Python', 'OK', 'Information') | Out-Null
                Start-Process 'https://www.python.org/downloads/windows/'
                exit 0
            }
            & winget.exe install --id Python.Python.3.13 --exact --source winget --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
            if ($LASTEXITCODE -ne 0) { throw 'WinGet ne peut pas installer Python. Installez Python 3.13 depuis python.org/downloads/windows/ puis relancez.' }
            $basePython = Find-Python
            if (-not $basePython) { throw 'Python reste introuvable. Fermez cette fenetre et relancez INSTALLER.bat apres installation de Python 3.13 avec Tcl/Tk.' }
        }
        Write-Host '[1/3] Creation de l environnement local...' -ForegroundColor Cyan
        & $basePython -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw 'Creation impossible. Extrayez le projet dans un dossier accessible en ecriture, par exemple Documents.' }
    }
    & $pythonExe -c 'import sys, tkinter; assert (3,11) <= sys.version_info[:2] < (3,14); assert sys.maxsize > 2**32' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Le .venv existant doit utiliser Python 3.11 a 3.13 en 64 bits avec Tcl/Tk. Il est conserve. Renommez-le en venv-ancien puis relancez apres installation de Python.' }
    Write-Host '[1/3] Python et interface graphique : OK' -ForegroundColor Green
    & $pythonExe -m scripts.check_installation
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[2/3] Installation des composants (quelques minutes au premier lancement)...' -ForegroundColor Cyan
        & $pythonExe -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Installation interrompue. Verifiez Internet, l espace disque et le proxy, puis relancez INSTALLER.bat. Les donnees sont conservees.' }
    }
    & $pythonExe -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Les composants Python sont incompatibles. Le .venv est conserve. Renommez-le en venv-ancien puis relancez INSTALLER.bat.' }
    Write-Host '[2/3] Composants : OK' -ForegroundColor Green
    if ($PrepareOnly) { exit 0 }
    if ($Mode -ne 'Demo') {
        & $pythonExe -m scripts.first_run --needs-setup
        $setupState = $LASTEXITCODE
        if ($setupState -notin @(0, 1)) { throw 'Configuration illisible. Relancez INSTALLER.bat pour afficher les indications de recuperation.' }
        if ($Mode -eq 'Configure' -or $setupState -eq 1) {
            Write-Host '[3/3] Suivez les etapes dans la fenetre LoL Analytics.' -ForegroundColor Cyan
            & $pythonExe -m scripts.first_run
            $wizardResult = $LASTEXITCODE
            if ($wizardResult -eq 2) { exit 0 }
            if ($wizardResult -eq 3) { $Mode = 'Demo' }
            elseif ($wizardResult -ne 0) { throw 'Assistant interrompu. Relancez INSTALLER.bat ; aucun historique n est efface.' }
        }
    }
    Write-Host 'L application va s ouvrir dans votre navigateur. Gardez cette fenetre ouverte.' -ForegroundColor Cyan
    Write-Host 'Pour fermer l application : revenez ici et appuyez sur Ctrl+C.'
    if ($Mode -eq 'Demo') { & $pythonExe -m scripts.start_demo }
    else { & $pythonExe -m scripts.start_personal }
    if ($LASTEXITCODE -ne 0) { throw 'L application s est arretee. Consultez le message ci-dessus, puis relancez le raccourci. Aucune reinitialisation automatique.' }
}
catch {
    # Deliberately do not print arbitrary exceptions, environment variables or tokens.
    $message = if ($_.Exception -is [System.Management.Automation.RuntimeException]) { $_.Exception.Message } else { 'Preparation impossible. Verifiez les droits du dossier et relancez INSTALLER.bat. Aucun fichier personnel n est efface.' }
    Write-Host $message -ForegroundColor Red
    [System.Windows.Forms.MessageBox]::Show($message, 'LoL Analytics - Besoin de votre attention', 'OK', 'Warning') | Out-Null
    exit 1
}
