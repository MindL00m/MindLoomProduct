# Check the driver against real UI Automation: builds the "Loom Fixture" app with the C#
# compiler that ships with Windows, then runs the agent's --driver-selftest on it.
# Run it from a signed-in desktop and leave the keyboard and mouse alone while it runs
# (it brings the fixture to the front for a few seconds to type).
#
#   powershell -ExecutionPolicy Bypass -File scripts\driver-selftest.ps1
#
# Needs Python 3.12 with the requirements installed (pip install -r requirements.txt).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path $csc)) { throw "csc.exe (.NET Framework 4) was not found under $env:WINDIR\Microsoft.NET." }

$fixture = Join-Path $root "fixture\LoomFixture.exe"
& $csc /nologo /target:winexe "/out:$fixture" /r:System.Windows.Forms.dll /r:System.Drawing.dll (Join-Path $root "fixture\LoomFixture.cs")
if ($LASTEXITCODE -ne 0) { throw "Building the fixture failed." }

$python = if (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) { Join-Path $root ".venv\Scripts\python.exe" } else { "python" }
$log = Join-Path $root "selftest.log"
& $python -m mindloom_agent --driver-selftest $fixture $log
$code = $LASTEXITCODE
if (-not (Select-String -Path $log -Pattern "SELFTEST PASSED" -Quiet)) {
    Write-Host "Self-test failed. See $log"
    if ($code -eq 0) { $code = 1 }
}
exit $code
