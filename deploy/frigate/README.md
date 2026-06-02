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

## Pull active config into the repo

```bash
ssh pve4 "pct pull 240 /opt/frigate/config/config.stock-ab.yml /tmp/config.stock-ab.yml.frigate-repo-sync"
scp pve4:/tmp/config.stock-ab.yml.frigate-repo-sync /tmp/config.stock-ab.yml.frigate-repo-sync
cp /tmp/config.stock-ab.yml.frigate-repo-sync deploy/frigate/config.stock-ab.yml
git diff -- deploy/frigate/config.stock-ab.yml
```

Review the diff for secrets before committing.

## Push repo config to CT240

```bash
scp deploy/frigate/config.stock-ab.yml pve4:/tmp/config.stock-ab.yml.frigate-repo-sync
ssh pve4 "pct push 240 /tmp/config.stock-ab.yml.frigate-repo-sync /opt/frigate/config/config.stock-ab.yml"
ssh pve4 "pct exec 240 -- bash -lc 'docker restart frigate'"
```

After restart, verify:

```bash
ssh pve4 "pct exec 240 -- bash -lc 'curl -sf http://127.0.0.1:5000/api/config >/dev/null && docker ps --filter name=frigate --format \"{{.Names}} {{.Status}}\"'"
```
