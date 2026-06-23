# Deploying Steward to a real Proxmox cluster

This is the path from the mock demo to watching (and eventually acting on) a
**real** cluster — safely, in stages. The golden rule: **read-only first,
dry-run for a long time, allow-list last.** Nothing here flips a destructive
switch until you explicitly do.

> New to the project? Read the [README](README.md) and
> [`AGENTIC_SRE_PLAN.md`](AGENTIC_SRE_PLAN.md) first. The
> [Safety model](README.md#safety-model-guardrails) is the thing to internalize.

---

## 1. Where to run it

Steward is a small FastAPI + SQLite process — a 1-vCPU / 512 MB LXC is plenty.

**Run it _out-of-band_ from the cluster it watches.** A monitor that lives
inside the cluster dies exactly when the cluster is in trouble and can't tell
you. Good homes, best first:

| Host | Pros | Cons |
|---|---|---|
| A separate always-on box (Pi, NUC, a Mac) | survives a full cluster outage | one more thing to run |
| A dedicated LXC on the cluster | trivial to stand up | dies with the cluster — **only acceptable if alerting (§5) goes off-box** |

Whatever you pick, make sure the **alerting path (ntfy) leaves the box**, so
"the cluster is down" can still reach your phone.

---

## 2. Install

### Option A — systemd (bare metal / LXC)

```bash
sudo adduser --system --group --home /opt/steward steward
sudo -u steward git clone https://github.com/fungiblemoose/steward /opt/steward/app
cd /opt/steward/app/backend
sudo -u steward python3 -m venv .venv
sudo -u steward .venv/bin/pip install -e ".[proxmox]"   # [proxmox] pulls proxmoxer
# build the UI (optional; the API serves it from frontend/dist if present)
cd ../frontend && npm ci && npm run build

sudo cp /opt/steward/app/deploy/steward.service /etc/systemd/system/
sudoedit /opt/steward/.env        # see §3, §4
sudo systemctl daemon-reload && sudo systemctl enable --now steward
journalctl -u steward -f
```

A ready unit file is in [`deploy/steward.service`](deploy/steward.service).

### Option B — Docker

```bash
git clone https://github.com/fungiblemoose/steward && cd steward
cp .env.example .env && $EDITOR .env      # §3, §4
docker compose up -d --build
```

Mount a volume for `data/` so the SQLite history and audit trail survive
restarts (the compose file already does this).

---

## 3. Connect to the real cluster (read-only first)

### Create a least-privilege API token in Proxmox

1. **Datacenter → Permissions → Users**: add e.g. `monitor@pve` (or reuse one).
2. **Datacenter → Permissions → API Tokens → Add**: user `monitor@pve`,
   Token ID `steward`. Leave **Privilege Separation ON**. Copy the secret — it's
   shown **once**.
3. **Datacenter → Permissions → Add → API Token Permission**: grant the token
   the built-in **`PVEAuditor`** role on path `/` (read-only: covers
   `VM.Audit` / `Sys.Audit` / `Datastore.Audit`). **Stop here for now** — no
   migrate/power rights yet.

### Point Steward at it (still observing only)

```ini
# /opt/steward/.env
STEWARD_PROXMOX_MODE=real
STEWARD_PROXMOX_HOST=192.0.2.10          # a cluster node (or a VIP)
STEWARD_PROXMOX_PORT=8006
STEWARD_PROXMOX_USER=monitor@pve
STEWARD_PROXMOX_TOKEN_NAME=steward
STEWARD_PROXMOX_TOKEN_VALUE=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
STEWARD_PROXMOX_VERIFY_SSL=false         # true once you trust the cert chain

# Safety: keep these as-is for the burn-in.
STEWARD_DRY_RUN=true                      # nothing mutates
STEWARD_ACTION_ALLOWLIST=                 # empty => nothing auto-acts
```

Restart, then confirm the collector sees real data:

```bash
curl -s localhost:8080/api/state | jq '.snapshot.nodes[] | {node, cpu_pct, mem_pct}'
```

Your real nodes/VMs should appear and update. The built-in checks (CPU/mem
pressure, VM stopped, storage full, quorum) now evaluate against live metrics
and will fire events + log **suggested** actions into the approval queue — but
with dry-run ON and an empty allow-list, **nothing executes.** Let it run like
this for a while and sanity-check that events match reality.

---

## 4. Out-of-band alerting (do this before anything else)

```ini
STEWARD_NOTIFY_KIND=ntfy
STEWARD_NOTIFY_NTFY_URL=https://ntfy.sh/your-private-topic-here
```

Warning/critical events now push to your phone via the ntfy app. Verify it works
(drive a check, or temporarily lower a threshold) **before** you trust Steward to
run unattended.

Lock the UI down too:

```ini
STEWARD_AUTH_TOKEN=$(openssl rand -hex 24)   # shared bearer token for UI/API
```

Put it behind your reverse proxy / VPN; don't expose `:8080` to the internet.

---

## 5. Turn on the balancer (Tier 0) — staged

Only after the burn-in in §3 looks correct. Each step is deliberate:

1. **Enable the check** (it ships disabled):
   ```bash
   curl -XPOST localhost:8080/api/checks/builtin.autonomous_balancer/toggle \
        -H "Authorization: Bearer $TOKEN"
   ```
   With dry-run still ON it now **previews** moves. Watch them:
   ```bash
   curl -s localhost:8080/api/balancer/simulate | jq
   ```
   or the **Dashboard → Cluster balance** card. Confirm the moves it proposes are
   ones you'd actually make.

2. **Allow-list the guests** you'll let it move — and **exclude HA-managed
   guests** (let the Proxmox HA manager own those):
   ```ini
   STEWARD_ACTION_ALLOWLIST=201,202,203     # VMIDs only
   ```

3. **Add the rights** the token needs to actually migrate, on the allow-listed
   guests' path: `VM.Migrate` (and `VM.PowerMgmt` / `VM.Config.Memory` if you
   want power/balloon actions). Keep `PVEAuditor` for reads.

4. **Flip dry-run off** when you trust it:
   ```ini
   STEWARD_DRY_RUN=false
   ```
   Now allow-listed guests actually live-migrate to balance load. The global
   **kill switch** (`STEWARD_PAUSED=true` or the header toggle) stops everything
   instantly; every action is in the audit log.

Tune with `STEWARD_BALANCER_*` (weights, target cap, min-improvement,
moves/cycle) — see [`.env.example`](.env.example).

---

## 6. Escalation to Claude Code (Tier 2) — optional

```ini
STEWARD_ESCALATION_WEBHOOK_URL=http://your-receiver:9000/incident
```

Repeated, unresolved incidents POST to that webhook. Run the worked receiver in
[`examples/escalation_receiver.py`](examples/escalation_receiver.py) and swap its
stub for a `claude -p` call so an agent investigates and **proposes** remediation
into the approval queue (it never executes directly). Off unless the URL is set.

---

## Pre-flight checklist before flipping dry-run off

- [ ] Collector shows correct live data for every node/VM (§3).
- [ ] ntfy alerts actually reach your phone (§4), from off-box.
- [ ] `UI/API` is behind auth + a VPN/reverse proxy (§4).
- [ ] Balancer **previews** (dry-run) have looked sane for a while (§5.1).
- [ ] Allow-list contains only guests you're happy to see move; **no HA guests**.
- [ ] Token has exactly the rights it needs, no more.
- [ ] You know where the kill switch is.

When all boxes are ticked, flip `STEWARD_DRY_RUN=false`. Start with one or two
low-stakes guests on the allow-list and widen from there.
