# Free PostgreSQL on the Windows PC

This prepares an **optional new database** on your stationary PC. PostgreSQL has no subscription charge. A PC with 32 GB RAM is sufficient for this small deployment; the database starts with a 2 GB memory limit, and the optional app has a separate 2 GB limit. Storage, electricity, internet and backups still have costs. Existing production PostgreSQL support stays in place; this setup uses neither Oracle nor MongoDB.

**Nothing here changes Railway, imports production data, deletes the old database or changes production `DATABASE_URL`.** Docker is not installed by the scripts. Runtime verification on your PC is a separate step after prerequisites are installed.

## 1. Prepare Windows once

1. Use a supported 64-bit Windows installation, hardware virtualization enabled in BIOS, and Linux containers. Install/update WSL2 (`wsl --install`, then `wsl --update` from an elevated PowerShell if needed) and restart when Windows asks.
2. Install Docker Desktop from its official Windows installer. Select the WSL2 backend, start Docker Desktop, and wait for its engine to become ready. Its current Windows requirements include WSL2 2.1.5 or later and at least 8 GB RAM; check your exact Windows build against the [official installation requirements](https://docs.docker.com/desktop/setup/install/windows-install/).
3. Docker Desktop is free for personal use, education, noncommercial open source and businesses below its employee/revenue thresholds. Larger commercial organizations and government use require a paid plan. Check the [current license conditions](https://docs.docker.com/subscription-billing/desktop-license/). Docker Engine in a maintained Linux installation is an alternative if Desktop's license does not fit; this guide targets Windows with Desktop.
4. Install Python 3.11 or later. Backups need `psycopg2-binary`, already in IntelliPlan's `requirements.txt`. Keep enough SSD space for the database, WSL virtual disk, images, retained backups and growth. Start with at least 20 GB free beyond your anticipated database size.

Use these commands when preparing the PC:

```powershell
wsl --version
docker version
docker compose version
python --version
python -m pip install psycopg2-binary
```

Docker must report a Linux server running Engine28 or later; older Engine releases had a localhost-port isolation issue. The launcher checks this. It also checks the selected Docker context and `DOCKER_HOST`/`DOCKER_CONTEXT` overrides, refuses TCP/SSH/remote endpoints, and pins every Docker operation to the validated local named pipe or Unix socket. A remote context cannot receive your backup archive. See [Docker's port-publishing guidance](https://docs.docker.com/engine/network/port-publishing/). Do not expose Docker's daemon API or open PostgreSQL's port in Windows Firewall/router settings.

## 2. Generate private configuration and start PostgreSQL

From your IntelliPlan checkout:

```powershell
python scripts/self_hosted_postgres.py init
python scripts/self_hosted_postgres.py start
python scripts/self_hosted_postgres.py status
```

PowerShell wrappers are also available: `./scripts/self_hosted_postgres.ps1 -Action start`.

The initializer generates three independent random secrets under `%LOCALAPPDATA%\IntelliPlan\PostgreSQL`, outside this repository and Docker's build context. It restricts the Windows directory ACL to your account, SYSTEM and Administrators. It prints paths, never passwords. Re-running initialization preserves credentials; a partial or incompatible setup fails rather than replacing passwords. Use an NTFS directory owned by this Windows account. A dedicated alternative is supported with `--state-dir D:\IntelliPlan-PostgreSQL`; use the same option on subsequent commands. Roots, repository paths, symlinks/junctions and relative paths are rejected.

The tracked configuration is `deployment/self-hosted-postgres/compose.json` (JSON is valid Compose configuration). The image is pinned to the supported [official `postgres:17.11-bookworm` tag](https://hub.docker.com/_/postgres), rather than an RC or a floating major upgrade. Its named volume is mounted at `/var/lib/postgresql/data`, the required persistence path for PostgreSQL17. The bootstrap `postgres` administrator and the application's non-superuser `intelliplan` role have separate credentials. The application does not get the administrator password. Host authentication uses SCRAM.

PostgreSQL binds to **127.0.0.1:5432 only**. The database container joins the internal network and, for Docker Desktop's WSL2 backend, the existing `outbound` network as well: Docker Desktop otherwise omits host-port forwarding for a container attached only to an internal network. This means the database container can make outbound connections, but the host port remains loopback-only; another device or a Railway service cannot connect to it. Never replace that binding with `0.0.0.0`, expose public 5432, or forward that port on the router.

For an administrative local SQL session, Docker Desktop's container **Exec** view can run `psql -U postgres -d intelliplan` as the Linux `postgres` user. The connection shape for a separate local app is `postgresql+psycopg2://intelliplan:<password-from-protected-file>@127.0.0.1:5432/intelliplan`; avoid putting the completed URL in shell history, screenshots or chat.

## 3. Choose where the app runs

### Recommended simple arrangement: app and database on the same PC

An optional Compose profile builds the existing application and supplies its database URL and session key from secret files inside the container. It also persists `instance` and `uploads` separately. To try a **new local instance**, with no production data imported:

```powershell
python scripts/self_hosted_postgres.py start-app
```

Open `http://localhost:8080` and check `http://localhost:8080/health`. This initializes the new local app schema. It does not replace the cloud app. Background reminder/followthrough jobs are disabled in this profile, preventing a second instance from duplicating those jobs during validation.

Edit the protected `app.env` file for settings and optional AI provider credentials. Before serving real users, set `APP_BASE_URL` to the exact public **HTTPS** origin, keep account/session secrets, configure a persistent HTTPS ingress, and update your OAuth callback registrations. Confirm health, schema flags, authentication, uploads, provider access and reminders at that public origin. A named Cloudflare Tunnel is one option for publishing only the **web** port without router port forwarding; it requires a Cloudflare account/domain setup and a separately authorized DNS/ingress change. See [Cloudflare's application publishing guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/). No tunnel or DNS changes are performed by this setup.

### Keeping the web app on Railway needs a separately verified private bridge

Railway's private network connects services **inside the same Railway project/environment**; it does not automatically include your home PC. See [Railway private networking](https://docs.railway.com/networking/private-networking).

A private VPN bridge could be engineered later, but this package does **not** claim that Tailscale plus a new `DATABASE_URL` is enough. Tailscale userspace mode exposes a SOCKS/HTTP proxy in restricted containers; the PostgreSQL driver must actually be routed through a supported TCP bridge, and the PC-side listener/ACLs, authentication, reconnects, startup ordering and outage behavior must be tested. A proxy environment variable alone is not a verified libpq tunnel. See [Tailscale userspace networking](https://tailscale.com/docs/concepts/userspace-networking).

Until such a bridge is demonstrated from the actual Railway service, keep Railway using its current database, or plan a separate move of the web app to the same PC. Do not publish the database to make that connection work.

## 4. Make a backup and prove a restore

```powershell
python scripts/self_hosted_postgres.py backup
python scripts/self_hosted_postgres.py restore-check --archive 'C:\Users\YOURNAME\AppData\Local\IntelliPlan\PostgreSQL\backups\intelliplan-TIMESTAMP.dump'
```

Use the exact archive path printed by `backup`. The script keeps a custom-format dump and same-name `.json` metadata. It exports a read-only repeatable-read PostgreSQL snapshot, computes each user table's row count/content fingerprint, and passes that snapshot to `pg_dump`. Live writes cannot make those two views disagree. Archive bytes use binary I/O, so Windows PowerShell cannot corrupt the dump through text redirection. Incomplete attempts remain `.partial` files and are never called verified backups. See [PostgreSQL dump consistency and snapshot options](https://www.postgresql.org/docs/17/app-pgdump.html).

`restore-check` validates the archive checksum first, creates a **new temporary container** without networking or published ports, restores in a single transaction with errors fatal, and compares the restored table inventory, row counts and content fingerprints. It stops only that new `--rm` container; the actual database, persistent volumes and backup files remain untouched. Temporary trust authentication is limited to this network-disabled verification container, which has no host secrets mounted. Only restore your own trusted archives: PostgreSQL explains that a dump can contain SQL chosen by its source superusers. See [pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html).

Verification uses at most 6 GB RAM, including a 4 GB temporary data filesystem. Larger databases need a reviewed disk-backed recovery environment; a failed verification never modifies the real database. Success creates a separate `-verified-...json` report. This proves that archive's tables restore; it is not a production cutover or a point-in-time/WAL recovery system.

Keep both dump and metadata, plus a protected copy of the secret/configuration directory, on **another physical device or encrypted off-machine storage**. The database dump does not back up uploaded files, filesystem sessions, global PostgreSQL roles or application configuration. Back up the app's `app_uploads` volume and required configuration separately. Test those restores too. Never put real backups or secrets in Git.

Schedule a daily backup under this Windows account only after a manual backup and restore check succeed. The supplied `scripts/self_hosted_postgres_backup_task.ps1` wrapper records the latest outcome at `C:\IntelliPlan-PostgreSQL\backup-last-result.txt` and exits nonzero on failure, which Task Scheduler records in Last Run Result and History. Keep several generations; no automatic retention/deletion is enabled. A named Docker volume and a backup on the same SSD are not protection against loss of the PC.

## 5. Production cutover is a separate operation

Before changing production `DATABASE_URL`, require all of the following:

- Inventory the live server version, extensions, user schemas, roles, RLS policies, database size and storage growth. This PostgreSQL17 image must support the source's features; a newer-source dump cannot be assumed compatible. Do not use the app profile to overwrite an existing target.
- Take an independent current source backup, restore it into an **isolated** target, and prove table/content and sequence agreement. The existing `scripts/migrate_postgres.py check --env-file <protected-file>` and `verify` commands are read-only comparison tools for its documented public-schema migration scope. They do not replace an inventory of additional schemas/extensions. Do **not** run its `copy`, `sync` or `--replace-target` commands as part of this setup.
- Plan a write-maintenance window for the final source snapshot; compare again while writes are stopped, exercise real login/task/tutor/parent/teacher paths on the candidate, and have a timed rollback to the unchanged source. Prove backup and upload restores independently.
- Verify the actual deployment's revision and healthy schema flags, DNS/HTTPS, authenticated network route, recovery after PC/reboot/network loss and background-job ownership before directing users to it.

Keep the source database, its backups and the old deployment throughout this process. There is no source deletion, migration or cutover command in the self-hosting launcher.

## Keep it running

Keep the PC awake, connected by reliable networking, and on stable power; a UPS is useful. Windows/Docker Desktop must be running after reboot—do not assume `restart: unless-stopped` starts Desktop before sign-in. Monitor SSD space and backup age. Apply supported Windows, Docker and PostgreSQL minor security updates after a verified backup; review the pinned image and setup metadata deliberately. Do not replace the17 data directory with an18 image: major upgrades require PostgreSQL's documented migration procedure.

To pause the local instance, use Docker Desktop **Stop**, which retains its named volumes. Avoid `docker compose down --volumes`, Docker volume prune, factory reset or deleting Docker/WSL data: those can remove persistent data. None are included in these scripts.
