$ErrorActionPreference = "Stop"

$root = "C:\Users\Stephen Sun\Documents\Codex\2026-06-24\dow"
$odoo = Join-Path $root "odoo-git"
$pgBin = "C:\Program Files\PostgreSQL\17\bin"
$pgData = Join-Path $root "work\pgdata-odoo"
$pgLog = Join-Path $root "work\pgdata-odoo.log"
$wkhtmlBin = Join-Path $root "work\tools\wkhtmltox\wkhtmltox\bin"

$env:Path = "$wkhtmlBin;$env:Path"

& (Join-Path $pgBin "pg_isready.exe") -h localhost -p 55432 -U odoo | Out-Null
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $pgBin "pg_ctl.exe") -D $pgData -l $pgLog -o "-p 55432" start
}

Set-Location $odoo
& ".\.venv\Scripts\python.exe" "odoo-bin" "server" "-c" ".\odoo-dev.conf" "-d" "odoo_test"
