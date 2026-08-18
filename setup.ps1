param(
    [switch]$SkipDev
)

$ErrorActionPreference = "Stop"

function Resolve-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "No se encontró Python en PATH. Instala Python 3.10+ e inténtalo de nuevo."
}

$pyCmd = Resolve-Python
$venvPath = ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

Write-Host "[setup] Creando entorno virtual en $venvPath..."
& $pyCmd[0] @($pyCmd[1..($pyCmd.Length-1)]) -m venv $venvPath

Write-Host "[setup] Actualizando pip..."
& $venvPython -m pip install --upgrade pip

Write-Host "[setup] Instalando dependencias runtime..."
& $venvPython -m pip install -r requirements.txt

if (-not $SkipDev) {
    Write-Host "[setup] Instalando dependencias de desarrollo..."
    & $venvPython -m pip install -r requirements-dev.txt
}

if (-not (Test-Path ".env")) {
    Write-Host "[setup] Generando .env desde .env.example..."
    Copy-Item ".env.example" ".env"
}

$envContent = Get-Content ".env" -Raw
if ($envContent -notmatch "(?m)^ENCRYPTION_KEY=") {
    Write-Host "[setup] Generando ENCRYPTION_KEY y añadiéndola a .env..."
    $key = & $venvPython -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    Add-Content ".env" ""
    Add-Content ".env" "# Clave de cifrado local (generada automáticamente)"
    Add-Content ".env" "ENCRYPTION_KEY=$key"
}

Write-Host ""
Write-Host "[setup] OK. Siguientes pasos:"
Write-Host "  1) Completa GARMIN_EMAIL/GARMIN_PASSWORD y claves LLM en .env"
Write-Host "  2) Configura SUPABASE_URL y SUPABASE_ANON_KEY"
Write-Host "  3) Ejecuta: .venv\Scripts\python.exe -m agent.main"
