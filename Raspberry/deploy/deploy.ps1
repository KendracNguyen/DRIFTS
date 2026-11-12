# Copy DRIFTS from your Windows laptop to the Pi Zero, then restart the app.
#
#   From the repo folder (DRIFTS-repo) in PowerShell:
#     .\Raspberry\deploy\deploy.ps1                      # copy + restart
#     .\Raspberry\deploy\deploy.ps1 -Install             # first time: copy + run install.sh
#     .\Raspberry\deploy\deploy.ps1 -Install -Mesh -Whisplay
#                                                        # ... with the face mesh and the Whisplay HAT driver
#
# On the Pi this gives the same layout as a git clone:
#     ~/drifts/Raspberry   the app (its .venv is kept between deploys)
#     ~/drifts/Voice       the voice clips the Whisplay speaker plays
#
# If PowerShell blocks the script, run it once with:
#     powershell -ExecutionPolicy Bypass -File .\Raspberry\deploy\deploy.ps1
param(
    [string]$PiHost = "drifts.local",
    [string]$User = "drifts",
    [switch]$Install,
    [switch]$Mesh,          # MediaPipe face mesh (camera: all eight face signals)
    [switch]$Whisplay       # PiSugar Whisplay HAT driver (LCD, LED, speaker, button)
)
$ErrorActionPreference = "Stop"

$app    = Split-Path -Parent $PSScriptRoot          # ...\DRIFTS-repo\Raspberry
$root   = Split-Path -Parent $app                   # ...\DRIFTS-repo
$tgz    = Join-Path $env:TEMP "drifts-deploy.tgz"
$target = "$User@$PiHost"

Write-Host "==> Packing Raspberry and Voice from $root"
tar -czf $tgz --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=captures `
    --exclude=logs -C $root Raspberry Voice
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

Write-Host "==> Copying to $target"
scp $tgz "${target}:/tmp/drifts-deploy.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed - can you 'ssh $target'?" }

# Unpack into ~/drifts (keeps the Pi's .venv, logs and profiles), and strip
# Windows line endings so the shell scripts and service file work on Linux.
$remote = "mkdir -p ~/drifts && tar -xzf /tmp/drifts-deploy.tgz -C ~/drifts " +
          "&& rm /tmp/drifts-deploy.tgz " +
          "&& find ~/drifts -path '*/.venv' -prune -o -type f " +
          "\( -name '*.sh' -o -name '*.service' -o -name '*.py' -o -name '*.toml' -o -name '*.txt' -o -name '*.json' \) " +
          "-exec sed -i 's/\r$//' {} +"
if ($Install) {
    $envs = ""
    if ($Mesh) { $envs += "MESH=1 " }
    if ($Whisplay) { $envs += "WHISPLAY=1 " }
    $remote += " && cd ~/drifts/Raspberry && ${envs}bash deploy/install.sh"
} else {
    $remote += " && (sudo systemctl restart drifts 2>/dev/null && echo 'Restarted drifts service' || echo 'Service not installed yet: run with -Install')"
}

Write-Host "==> Unpacking on the Pi"
ssh -t $target $remote
Remove-Item $tgz -ErrorAction SilentlyContinue
Write-Host "==> Done. Watch the log with:  ssh $target journalctl -u drifts -f"
