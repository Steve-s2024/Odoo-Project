$ErrorActionPreference = "Stop"

$root = "C:\Users\Stephen Sun\Documents\Codex\2026-06-24\dow"
$pgBin = "C:\Program Files\PostgreSQL\17\bin"
$pgData = Join-Path $root "work\pgdata-odoo"

& (Join-Path $pgBin "pg_ctl.exe") -D $pgData stop
