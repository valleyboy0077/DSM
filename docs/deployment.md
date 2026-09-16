# Deployment

The Docker image is the DSM deployment artifact. Build it with
`make docker-build` (or `./scripts/docker-build.sh`) and deploy the checked-in
Compose definition with `make docker-up`.

## Requirements

- Docker Engine with Docker Compose v2.24.4 or newer (needed for the optional
  LAN override)
- A local, untracked `.env` copied from `.env.example`, or exported variables
- Non-placeholder `DSM_ENCRYPTION_KEY` and `DSM_BOOTSTRAP_ADMIN_PASSWORD`

Keep `DSM_ENCRYPTION_KEY` unchanged for the lifetime of encrypted credentials;
changing it makes previously stored iDRAC passwords unreadable. Bootstrap
credentials are needed when a fresh persistent database has no users. Neither
value belongs in Git or command output.

Compose persists SQLite at `/var/lib/dsm/dsm.db` in the named `dsm_data` volume.
Routine `docker compose down` or container replacement preserves this volume.
Back it up before a production upgrade. Only `docker compose down --volumes`
removes it and therefore discards DSM data.

Deploy and observe a release:

```bash
make docker-config
make docker-up
curl http://127.0.0.1:8080/health
make docker-logs
```

To stop DSM while retaining its data, run `make docker-down`. For rollback,
retag a previously built local image as `dell-server-manager:local`, then run
`docker compose up --no-build --detach` and confirm `/health`; preserve the
volume and encryption key. If a deployment must be stopped immediately, use
`make docker-down`. Do not remove volumes unless intentionally retiring the
installation.

The committed Compose ports are loopback-only. For a trusted LAN, follow the
operator override procedure in [development.md](development.md); use a firewall
or authenticated TLS reverse proxy before making DSM remotely reachable.
