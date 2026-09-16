# Contributing

Every DSM change must remain Docker-buildable and be verified on the container
path before review. Run `make test`, `make docker-build`, and (with placeholder
values only for configuration validation) `make docker-config`. For deployment
changes, also validate `make docker-up` in a safe environment with real local
secrets. See [the development workflow](docs/development.md) for the canonical
Git → test → Docker build → Compose deploy process.
