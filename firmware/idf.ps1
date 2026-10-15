# Runs idf.py for firmware\official with the locally installed ESP-IDF 6.1.0 (PlatformIO) and Espressif toolchain.
# Usage: .\firmware\idf.ps1 build | .\firmware\idf.ps1 -p COM6 flash | .\firmware\idf.ps1 -p COM6 monitor
$idf = 'C:\Users\PC\.platformio\packages\framework-espidf'
$venv = 'C:\Users\PC\.platformio\penv\.espidf-6.1.0'
$toolchain = 'C:\Espressif\tools\xtensa-esp-elf\esp-15.2.0_20251204\xtensa-esp-elf\bin'

$env:IDF_PATH = $idf
$env:IDF_TOOLS_PATH = 'C:\Espressif\tools'
$env:IDF_PYTHON_ENV_PATH = $venv
$env:ESP_IDF_VERSION = '6.1.0'
$env:ESP_ROM_ELF_DIR = 'C:\Espressif\tools\esp-rom-elfs\20241011'
$env:PYTHONUTF8 = '1'
$env:PATH = "$toolchain;C:\Users\PC\.platformio\packages\tool-ninja;C:\Espressif\tools\cmake\4.0.3\bin;$venv\Scripts;$idf\tools;$env:PATH"

Push-Location (Join-Path $PSScriptRoot $(if ($env:FW_PROJECT) { $env:FW_PROJECT } else { 'official' }))
try {
    & "$venv\Scripts\python.exe" "$idf\tools\idf.py" @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
