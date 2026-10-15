# Runbook

Operational notes. The full runbook (report format changes, late data, failed jobs, model rollback) arrives with M6-T8.

## Production server (ADR-002)

- **Host:** Azure for Students subscription, resource group `gridcast-rg`, VM `gridcast-vm`, region Canada Central (Toronto), size `Standard_B2pts_v2` (2 Arm vCPU, 1 GB RAM, plus 2 GB swap), Ubuntu 24.04 LTS, 64 GB Premium SSD, static Standard public IP.
- **Access:** SSH as `gridcast` with the deploy key `~/.ssh/gridcast_deploy`. Key-only login; fail2ban protects SSH. The address lives in git-ignored `.env` as `GRIDCAST_SERVER`.
- **(Re)build:** `make server-bootstrap` runs the idempotent `scripts/server/bootstrap.sh` over SSH. It updates the code and dependencies from GitHub `main`.
- **Cost:** covered by the $100/year student credit (expires 2027-10-15, renewable while a student). The subscription has no card attached, so nothing can be charged beyond the credit. Spend is visible in the Azure portal under Cost Management.

## Live collector

- Runs as the systemd timer `gridcast-collector.timer`, hourly at :07 UTC (`Persistent=true` catches up after downtime).
- `make server-status`: timer state and the last run's log lines.
- `make server-gaps`: gap report (M1-T3 acceptance: 7 consecutive gap-free days).
- Logs: `journalctl -u gridcast-collector.service` on the server.
- Data: `~/gridcast/data/bronze/` (gzip files plus `_manifest.sqlite`).
- The macOS LaunchAgent (`make collector-install`) remains as a fallback if the server is down. Don't run both at once.
