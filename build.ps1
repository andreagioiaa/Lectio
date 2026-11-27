# Lectio - build automatica dell'eseguibile one-file.
# Uso:  .\build.ps1
#       .\build.ps1 -SkipTests

param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Tutto l'output finisce anche in build.log, cosi' resta leggibile dopo la chiusura.
$logFile = Join-Path $PSScriptRoot "build.log"
try { Start-Transcript -Path $logFile -Force | Out-Null } catch { }

try {
    Write-Host "Log completo: $logFile" -ForegroundColor DarkGray
    Write-Host "PowerShell $($PSVersionTable.PSVersion) - cartella: $PSScriptRoot" -ForegroundColor DarkGray
    $venvPath = Join-Path $PSScriptRoot ".venv"
    $venvPython = Join-Path $venvPath "Scripts\python.exe"

    Write-Host "== Lectio build ==" -ForegroundColor Cyan

    if (-not (Test-Path $venvPython)) {
        Write-Host "Creo il virtual environment con Python 3.12..." -ForegroundColor Yellow
        $py = Get-Command py -ErrorAction SilentlyContinue
        if ($py) {
            & py -3.12 -m venv $venvPath
        }
        else {
            $python = Get-Command python -ErrorAction SilentlyContinue
            if (-not $python) {
                throw "Python non trovato. Installa Python 3.12 a 64 bit e riprova."
            }
            & python -m venv $venvPath
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
            throw "Creazione del virtual environment non riuscita."
        }
    }

    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Aggiornamento di pip non riuscito."
    }

    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Installazione delle dipendenze non riuscita."
    }

    if (-not $SkipTests) {
        Write-Host "Eseguo i test..." -ForegroundColor Yellow
        & $venvPython -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "Test falliti: build interrotta."
        }
    }

    if (-not (Test-Path "assets\app.ico")) {
        throw "Icona assets\app.ico mancante."
    }

    # version_info.txt (metadati dell'exe) si ricava da APP_VERSION in config.py.
    & $venvPython tools\release.py version-info
    if ($LASTEXITCODE -ne 0) {
        throw "Generazione di version_info.txt non riuscita."
    }

    Write-Host "Compilazione con PyInstaller..." -ForegroundColor Yellow
    & $venvPython -m PyInstaller --noconfirm --clean Lectio.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller ha restituito un errore."
    }

    $exe = Join-Path $PSScriptRoot "dist\Lectio.exe"
    if (-not (Test-Path $exe)) {
        throw "Build terminata ma dist\Lectio.exe non esiste."
    }

    # Pandoc per l'esportazione Word: copiato accanto all'exe (non dentro, per non
    # estrarre ~150 MB a ogni avvio). Lectio lo cerca in quella cartella.
    $ErrorActionPreference = "Continue"
    # pypandoc-binary 1.17 restituisce il percorso senza ".exe": si cerca il file reale.
    $pandocSrc = & $venvPython -c "import pathlib, pypandoc; p = pathlib.Path(pypandoc.get_pandoc_path()); c = [x for x in (p, p.with_name(p.name + '.exe')) if x.is_file()]; print(c[0] if c else '')" 2>$null
    $pandocExit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"

    if ($pandocExit -eq 0 -and $pandocSrc -and (Test-Path $pandocSrc)) {
        Copy-Item $pandocSrc (Join-Path $PSScriptRoot "dist\pandoc.exe") -Force
        Write-Host "Pandoc copiato in dist\pandoc.exe" -ForegroundColor DarkGray
    }
    else {
        Write-Host "Pandoc non trovato: l'esportazione .docx richiedera' pandoc nel PATH." -ForegroundColor Yellow
    }

    $sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Build completata: $exe ($sizeMb MB)" -ForegroundColor Green
    Write-Host "Avvialo normalmente: l'app resta asInvoker e chiedera' UAC solo al primo blocco siti." -ForegroundColor DarkGray
}
catch {
    Write-Host ""
    Write-Host "=== BUILD FALLITA ===" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host $_.InvocationInfo.PositionMessage -ForegroundColor DarkRed
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
}
finally {
    try { Stop-Transcript | Out-Null } catch { }
    Write-Host ""
    Write-Host "Log salvato in: $logFile" -ForegroundColor DarkGray
    Read-Host "Premi INVIO per chiudere la finestra"
}