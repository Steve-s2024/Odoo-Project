$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pgBin = "C:\Program Files\PostgreSQL\17\bin"
$pgData = Join-Path $root "work\pgdata-odoo"

& (Join-Path $pgBin "pg_ctl.exe") -D $pgData stop
