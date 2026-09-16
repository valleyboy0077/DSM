.DEFAULT_GOAL := test

.PHONY: test docker-build docker-config docker-up docker-down docker-logs

test:
	.venv/bin/python -m pytest -q

docker-build:
	./scripts/docker-build.sh

docker-config:
	docker compose config

docker-up:
	./scripts/docker-deploy.sh

docker-down:
	docker compose down

docker-logs:
	docker compose logs --follow dsm
