# Frigate deployment config

This directory tracks the CT240 Frigate deployment config.

Active runtime path:

```text
/opt/frigate/config/config.yml -> /opt/frigate/config/config.stock-ab.yml
```

Tracked source of truth:

```text
deploy/frigate/config.stock-ab.yml
```

The config should keep credentials as Frigate environment placeholders such as
`{FRIGATE_REOLINK_PASS}`, `{FRIGATE_MQTT_PASSWORD}`, and
`{FRIGATE_GEMINI_API_KEY}`. Do not commit literal camera passwords, MQTT
passwords, API keys, tokens, or database credentials.

## Normal workflow

Use this loop for config experiments:

1. Edit `deploy/frigate/config.stock-ab.yml`.
2. Run `deploy/frigate/diff-active-config.sh` to see how it differs from CT240.
3. Run `deploy/frigate/deploy-config.sh` to back up the active config, deploy, restart, and verify.
4. If Frigate is healthy, commit and push the config change.
5. If the test fails, run `deploy/frigate/rollback-config.sh <backup-path>` using the backup path printed by deploy.

For Gemini notification prompt tuning, make small changes and commit them with
messages like `Tune Duo3 notification prompt`. Avoid changing retention,
storage, and notification prompts in the same commit.

## Pull active config into the repo

```bash
deploy/frigate/pull-active-config.sh
```

Review the diff for secrets before committing.

## Push repo config to CT240

```bash
deploy/frigate/deploy-config.sh
```

The deploy script prints the backup path it created on CT240.

## Push selected code files to CT240

```bash
deploy/frigate/deploy-code.sh
```

This deploys repo-managed runtime patches to CT240, updates
`/opt/frigate/docker-compose.yml`, recreates the `frigate` container, waits for
health, and verifies Duo3 VOD is using the mobile playback stream. The script
backs up changed CT240 files under `/opt/frigate/deploy-backups/YYYYMMDD-HHMMSS`.

Use this for small Python runtime fixes such as `frigate/api/media.py`. For
larger Frigate source changes, reconcile CT240 first and prefer a full image
build/deploy plan.

Roll back a code deployment:

```bash
deploy/frigate/rollback-code.sh /opt/frigate/deploy-backups/YYYYMMDD-HHMMSS
```

## Compare repo config with CT240

```bash
deploy/frigate/diff-active-config.sh
```

## Roll back a bad config test

```bash
deploy/frigate/rollback-config.sh /opt/frigate/config/config.stock-ab.yml.bak-YYYYMMDD-HHMMSS
```
