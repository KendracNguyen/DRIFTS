# Copy this repo from your Windows laptop to the Pi, then restart the app.
#
#   From the repo folder in PowerShell:
#     .\deploy\deploy.ps1                 # copy + restart
#     .\deploy\deploy.ps1 -Install        # first time: copy + run install.sh
#
# If PowerShell blocks the script, run it once with:
#     powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1
param(
    [string]$PiHost = "drifts.local",
    [string]$User = "drifts",
    [switch]$Install
)
$ErrorActionPreference = "Stop"

$repo   = Split-Path -Parent $PSScriptRoot
$parent = Split-Path -Parent $repo
$name   = Split-Path -Leaf $repo
$tgz    = Join-Path $env:TEMP "drifts-deploy.tgz"
$target = "$User@$PiHost"

Write-Host "==> Packing $repo"
tar -czf $tgz --exclude=.venv --exclude=__pycache__ --exclude=.git --exclude=.pytest_cache -C $parent $name
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

Write-Host "==> Copying to $target"
scp $tgz "${target}:/tmp/drifts-deploy.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed - can you 'ssh $target'?" }

# Unpack into ~/drifts (keeps the Pi's .venv), and strip Windows line endings
# so the shell scripts and service file work on Linux.
$remote = "mkdir -p ~/drifts && tar -xzf /tmp/drifts-deploy.tgz -C ~/drifts --strip-components=1 " +
          "&& rm /tmp/drifts-deploy.tgz " +
          "&& find ~/drifts -path ~/drifts/.venv -prune -o -type f " +
          "\( -name '*.sh' -o -name '*.service' -o -name '*.py' -o -name '*.toml' -o -name '*.txt' \) " +
          "-exec sed -i 's/\r$//' {} +"
if ($Install) {
    $remote += " && bash ~/drifts/deploy/install.sh"
} else {
    $remote += " && (sudo systemctl restart drifts 2>/dev/null && echo 'Restarted drifts service' || echo 'Service not installed yet: run with -Install')"
}

Write-Host "==> Unpacking on the Pi"
ssh -t $target $remote
Remove-Item $tgz -ErrorAction SilentlyContinue
Write-Host "==> Done. Watch the log with:  ssh $target journalctl -u drifts -f"
