# dockerized-imap-sync

This container reproduces the behavior of the Kubernetes `CronJob` in `imap-cron.yml` by running `imapsync` every 3 minutes inside a standalone Docker container.

## Runtime environment variables

The container expects these variables at runtime:

- `IMAP1_HOST`
- `IMAP1_USER`
- `IMAP1_PASSWORD`
- `IMAP2_HOST`
- `IMAP2_USER`
- `IMAP2_PASSWORD`

Defaults are baked in for:

- `IMAP1_HOST=imap.mail.yahoo.com`
- `IMAP2_HOST=imap.gmail.com`

You should still pass all variables explicitly when running the container.

## Build

```bash
docker build -t dockerized-imap-sync .
```

## Run

```bash
docker run -d \
  --name dockerized-imap-sync \
  -e IMAP1_HOST='imap.mail.yahoo.com' \
  -e IMAP1_USER='your-source-user' \
  -e IMAP1_PASSWORD='your-source-password' \
  -e IMAP2_HOST='imap.gmail.com' \
  -e IMAP2_USER='your-destination-user' \
  -e IMAP2_PASSWORD='your-destination-password' \
  dockerized-imap-sync
```

## Behavior

- Runs `imapsync` every 3 minutes using `cron`
- Uses the same sync flags as the original Kubernetes job
- Fails fast if any required runtime variables are missing

## GitHub Actions

The repository includes a workflow at `.github/workflows/build-image.yml` that builds and pushes the image to GHCR on every push to `main` and on manual dispatch.

Add this repository secret before using the workflow:

- `GHCR_PAT`: a GitHub personal access token with permission to push packages to GHCR

The workflow publishes:

- `ghcr.io/<owner>/dockerized-imap-sync:latest`
- `ghcr.io/<owner>/dockerized-imap-sync:sha-<commit>`
