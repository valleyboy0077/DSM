# Development workflow

Docker is DSM's canonical deployment path. Make application changes in Git,
verify them with the repository test suite, build the image, then deploy through
Compose:

```bash
git status
make test
make docker-build
cp .env.example .env  # first deployment only; edit locally with real values
make docker-config
make docker-up
```

`make test` uses the repository virtual environment and runs the complete
configured Python suite. `make docker-build` builds the image from the checked-in
`Dockerfile` without runtime secrets. `make docker-config` validates the checked-in
`compose.yaml` after Compose resolves the required environment. `make docker-up`
uses `scripts/docker-deploy.sh`, which requires `DSM_ENCRYPTION_KEY` and
`DSM_BOOTSTRAP_ADMIN_PASSWORD` from the shell environment or an untracked `.env`.
It never displays their values.

The committed `compose.yaml` is deliberately safe: DSM and MCP bind only to
loopback, and the application database is in the named `dsm_data` volume. An
operator who intentionally needs LAN access should copy
`compose.lan.yaml.example` to `compose.override.yaml`, choose only the required
port bindings, and enforce network access controls. `compose.override.yaml` is
ignored by Git so operator-specific LAN exposure cannot be committed by mistake.
The example requires Docker Compose 2.24.4 or newer to replace the loopback
binding safely. Do not expose MCP to an untrusted network.

Useful day-to-day commands are `make docker-logs` and `make docker-down`.
Pass extra Compose options to the wrapper when needed, for example
`./scripts/docker-deploy.sh --force-recreate`.
