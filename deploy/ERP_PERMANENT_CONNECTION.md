# ERP permanent connection

The canonical administrative connection to the production ERP is the SSH host
alias `sun-erp-prod`, defined in `deploy/erp-ssh.conf`.

This is a durable, key-based connection rather than a continuously open TCP
session.  It survives terminal closure, local restarts, and ERP restarts.  SSH
keep-alives detect broken connections during long-running deployments.

## Connection identity

- ERP address: `49.233.77.243`
- SSH user: `root`
- Dedicated local key: `~/.ssh/id_ed25519_tencent_codex`
- Authorized key fingerprint:
  `SHA256:uAIlNwyFWdmfBWwnx0YofzH8hEnsHja8IHbducqMS30`
- Pinned ERP ED25519 host fingerprint:
  `SHA256:4xYwzmlAVhKRVILMmZlKMaFsqOL0kMHMjaKVewh9rts`

The private key and API credentials must never be copied into this repository.

## Health check

Run from the repository root:

```powershell
.\deploy\erp-connection.ps1 -Action Health
```

This verifies SSH, the enabled and active Odoo service, and the ERP Shop API
health endpoint.

## Interactive maintenance

```powershell
.\deploy\erp-connection.ps1 -Action Shell
```

## Future ERP updates

Every future ERP-side update should use this wrapper:

```powershell
.\deploy\erp-connection.ps1 -Action Run `
    -ScriptPath .\tmp\deploy-release.sh `
    -UploadPath .\tmp\release.tar.gz
```

Uploaded files are placed in the directory exposed to the release script as
`$CODEX_ERP_RELEASE_DIR`.  The wrapper automatically:

1. uses strict host-key verification;
2. creates an isolated remote staging directory;
3. backs up the `odoo_prod` PostgreSQL database and current custom addons;
4. runs the supplied release script;
5. verifies Odoo and `/api/v1/health`; and
6. removes the temporary staging directory.

Release scripts remain responsible for narrowly upgrading the affected modules,
restoring the Odoo service in a failure trap, and performing feature-specific
tests.  A release that also changes the storefront must separately back up and
verify the storefront database and filestore.

## Shop-to-ERP runtime channel

Administrative SSH and the runtime Shop API are separate security boundaries.
The Hong Kong shop calls ERP over authenticated HTTPS with bounded timeouts and
idempotency keys.  PostgreSQL is not exposed, and the ERP SSH private key is
never installed in the shop.

The Hong Kong shop has its own production API client and key in a root-owned
service environment file.  Key rotation must retain an overlap window until
the replacement channel passes health, reservation, order, and payment tests;
then revoke the retired key.  Never convert runtime credentials into
non-expiring secrets.
