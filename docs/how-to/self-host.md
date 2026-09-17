# How to self-host

Publish Tracerator on a host you control. Set every deploy variable yourself. This repository does not ship a default hostname.

## Compose on the host

```bash
git clone https://github.com/discoposse/tracerator.git /opt/tracerator
cd /opt/tracerator
cp .env.example .env
# edit .env: HF_TOKEN and any TRACERATOR_*_DIR mounts
docker compose -f docker-compose.prod.yml up -d --build
```

`docker-compose.prod.yml` names the container `tracerator` and publishes `${TRACERATOR_PUBLISH_PORT:-8008}`.

Put a reverse proxy in front if you need TLS. `deploy/Caddyfile.example` is a starting Caddy site block:

```caddy
tracerator.example.com {
	encode zstd gzip
	reverse_proxy tracerator:8008
}
```

## SSH archive deploy

Required environment: `DEPLOY_HOST`, `DEPLOY_DIR`, `DEPLOY_URL`. Optional: `DEPLOY_USER` (default `root`), `DEPLOY_BARE` (default `${DEPLOY_DIR}.git`).

```bash
export DEPLOY_HOST=host.example
export DEPLOY_DIR=/opt/tracerator
export DEPLOY_URL=https://tracerator.example
./scripts/deploy-prod.sh
```

The script streams `git archive` over SSH. Untracked files, including `.env`, stay on the laptop. The remote `.env` is left in place.

One-time git remote on the host:

```bash
./scripts/deploy-prod.sh --bootstrap
git push -u prod main
```

## Extra baselines on the host

Do not copy macOS `/Users/...` paths onto Linux. Fetch LFS payloads next to the checkout or set `TRACERATOR_*_DIR` in the host `.env`.
