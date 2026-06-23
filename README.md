# 🛡️ Steward

**A local-LLM Proxmox babysitter.** Steward continuously watches a Proxmox
cluster with plain, deterministic code, and gives you a natural-language control
plane on top — a web dashboard, a chat panel, alerts in human language, and
guarded, auditable corrective actions like VM migration.

> _The repo is named `watchclawd`; the project/codebase is **Steward**._

> **The LLM is never in the hot path.** All polling, threshold evaluation, and
> action execution is done by deterministic code. A small local LLM (any
> OpenAI-compatible endpoint, e.g. [Ollama](https://ollama.com)) is used *only*
> to (a) turn natural language into structured checks, (b) answer questions
> about current state, (c) explain alerts, and (d) *suggest* actions for human
> approval. Steward works fully with no LLM configured at all.

This repository ships with a **mock Proxmox simulator** so you can clone, run,
and see the whole thing working — including a simulated VM migration through the
approval queue — **without touching any real infrastructure**. Everything
defaults to **dry-run** and the **mock** backend.

---

## Quickstart (Docker)

```bash
git clone <this-repo> watchclawd && cd watchclawd
docker compose up --build
# open http://localhost:8080
```

You'll see a 3-node simulated cluster updating live. Go to **Dashboard → Demo ·
load injector**, push a VM to 100% CPU, and watch a "Node CPU pressure" event
fire and a migration suggestion land in the **Actions → Approval queue**.
Approve it and the simulator moves the VM (in dry-run, fully audited).

No secrets are required for the demo.

## Quickstart (local dev)

Backend:

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest          # 52 tests, all green
python -m steward         # serves API on :8080 (built SPA if present)
```

Frontend (hot-reload dev server, proxies the API to :8080):

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173
```

For a single-process deploy, `npm run build` then run the backend — FastAPI
serves the built SPA from `frontend/dist`.

---

## Enabling the LLM (optional)

Steward speaks to any OpenAI-compatible `/chat/completions` endpoint. With
[Ollama](https://ollama.com):

```bash
ollama pull llama3.2:3b
# in your .env:
STEWARD_LLM_BASE_URL=http://localhost:11434/v1
STEWARD_LLM_MODEL=llama3.2:3b
```

Then the **Chat** tab answers questions grounded in live metrics ("what's under
pressure right now?"), the **Checks** tab turns English into checks ("alert me
if any node memory goes over 85%" → a reviewable, disabled check), and each
event gets an **Explain** button. Without an endpoint, these features degrade
gracefully and everything else keeps working.

---

## Architecture

```
  Web UI (React/Vite/Tailwind)
        │  REST + WebSocket
  FastAPI backend
   ├─ Collector        deterministic poll loop → metrics store + ring buffer
   ├─ Rule engine      pure threshold evaluation → events (per-target cooldown)
   ├─ Balancer (T0)    deterministic blended CPU+mem rebalancing → guarded moves
   ├─ Action executor  the only thing that mutates; layered guardrails
   ├─ Incidents (T2)   dedup/age repeated events → escalate the unresolved
   ├─ LLM service      async, off the loop: NL→check · Q&A · explain
   └─ SQLite           metrics (time-series) · checks · events · actions (audit)
        │
  Proxmox client (Protocol)
   ├─ Mock simulator   in-memory fake cluster + load injector  (default)
   └─ Real (proxmoxer) gated behind config; never run in this build
```

This is the deterministic spine of a **local agentic homelab SRE** — see
[`AGENTIC_SRE_PLAN.md`](AGENTIC_SRE_PLAN.md) for the full 3-tier design (Tier 0
autonomous balancing + Tier 2 escalation are built; the local investigator is
deferred). No LLM sits in any of these paths.

See [`ASSUMPTIONS.md`](ASSUMPTIONS.md) for design decisions and
[`backend/steward/`](backend/steward) for the modules.

### A check is data, not code

```jsonc
{
  "id": "builtin.node_cpu_pressure",
  "name": "Node CPU pressure",
  "probe_type": "proxmox_metric",     // or http_get, tcp_port, process_cpu, shell_command
  "target": "node:*",                  // node:<n> | vm:<id> | storage:* | cluster
  "condition": { "metric": "cpu_pct", "op": "gt", "threshold": 85 },
  "severity": "warning",
  "cooldown_s": 300,
  "auto_execute": false,               // suggestions go to the approval queue
  "suggested_action": { "type": "migrate", "params": { "strategy": "..." } }
}
```

Built-ins: node CPU pressure, node memory pressure, VM unexpectedly stopped,
storage near full, cluster quorum lost.

---

## Safety model (guardrails)

Every action — whether from a rule, the LLM, or a human — passes through one
executor that enforces, **in order**:

1. **Global kill switch.** When paused, nothing executes; suggestions are still
   logged. Toggle from the header.
2. **Dry-run (default ON).** Logs the fully-resolved action and a simulated
   outcome; never calls a mutating client method.
3. **Allow-list.** Only explicitly allow-listed guests are eligible for
   *automatic* action. A human approving a queued action may override this —
   human consent is the stronger gate.
4. **Cooldown + hourly rate limit** per action type.
5. **Approval gating.** LLM suggestions and any non-`auto_execute` check route to
   the approval queue and never auto-fire.
6. **Full audit.** Every proposed / executed / rejected / blocked action is a
   row with timestamp, trigger, resolved params, dry-run flag, before/after, and
   a reversibility note.

> **Migration notes (real backend):** live migration needs a valid online target
> and, for local disks, a storage migration; Steward refuses when no eligible
> target exists. Exclude HA-managed guests from the allow-list so Steward and the
> Proxmox HA manager don't fight. (Encoded as docs in `proxmox/real.py`.)

---

## Autonomous balancer (Tier 0)

A deterministic, **no-LLM** load balancer. When blended CPU+mem load imbalance
(stddev across nodes) crosses a threshold *and* is trending up, it picks the
guest+target that most reduce imbalance and live-migrates — through the same
guarded executor as everything else. Memory is a **hard** target-headroom
constraint; CPU has a cap too.

It ships **off**. Turning it on is a deliberate, staged opt-in:

1. **Enable** the `builtin.autonomous_balancer` check (Checks tab, or
   `POST /api/checks/builtin.autonomous_balancer/toggle`). With dry-run ON it now
   *previews* moves — watch the **Dashboard → Cluster balance** card and the audit
   log to see what it would do.
2. **Allow-list** the guests you'll let it move (`STEWARD_ACTION_ALLOWLIST` or the
   header). Keep HA-managed guests off the list.
3. **Flip dry-run off** when you trust it. Now allow-listed guests actually move.

`GET /api/balancer/simulate` returns the live imbalance + the moves it would make
without executing. Tuning knobs are `STEWARD_BALANCER_*` (weights, target cap,
min-improvement, moves/cycle, trend requirement).

## Escalation to Claude Code (Tier 2)

The "page a human only when the deterministic tiers can't cope" layer — rare by
design. Repeated, unresolved events for the same `(check, target)` are folded
into an **incident**; when one crosses `≥ min_occurrences` over `≥ min_age` and
isn't in cooldown, Steward POSTs a rich payload (incident + recent events + live
snapshot) to `STEWARD_ESCALATION_WEBHOOK_URL`. Wire that webhook to kick off a
**Claude Code run** that investigates via the read API and *proposes* remediation
into the approval queue — it never bypasses the guardrails. Off unless the
webhook is set. See [`examples/escalation_receiver.py`](examples/escalation_receiver.py)
for a worked receiver.

---

## Configuration

All config is environment-driven (prefix `STEWARD_`). Copy
[`.env.example`](.env.example) to `.env` and edit. **No secrets ever live in the
repo.** Highlights:

| Variable | Default | Meaning |
|---|---|---|
| `STEWARD_PROXMOX_MODE` | `mock` | `mock` simulator or `real` proxmoxer client |
| `STEWARD_DRY_RUN` | `true` | Master safety switch for action execution |
| `STEWARD_PAUSED` | `false` | Global kill switch |
| `STEWARD_ACTION_ALLOWLIST` | _(empty)_ | CSV of VMIDs eligible for auto-action |
| `STEWARD_POLL_INTERVAL_S` | `10` | Collector cadence |
| `STEWARD_METRICS_RETENTION_HOURS` | `72` | Time-series prune horizon |
| `STEWARD_BALANCER_WEIGHT_CPU` / `_MEM` | `0.5` / `0.5` | Blended-imbalance weights (Tier 0) |
| `STEWARD_BALANCER_MAX_TARGET_PCT` | `80` | Never migrate onto a node past this CPU/mem% |
| `STEWARD_ESCALATION_WEBHOOK_URL` | _(empty)_ | Tier-2 escalation target (blank = off) |
| `STEWARD_ESCALATION_MIN_OCCURRENCES` | `3` | Fires this many times before paging |
| `STEWARD_LLM_BASE_URL` | _(empty)_ | OpenAI-compatible endpoint (blank = off) |
| `STEWARD_AUTH_TOKEN` | _(empty)_ | Shared bearer token for UI/API (blank = open) |
| `STEWARD_NOTIFY_KIND` | `none` | `none` \| `ntfy` \| `webhook` |

---

## Running as a service / deploying to a real cluster

For the full, safety-staged path from the mock demo to watching (and eventually
acting on) a **real** Proxmox cluster — read-only token, dry-run burn-in,
out-of-band alerting, then the staged balancer rollout — see **[DEPLOY.md](DEPLOY.md)**.

Quick version: a ready systemd unit is in
[`deploy/steward.service`](deploy/steward.service) (run in a small LXC or any
always-on box), or `docker compose up -d`. Keep `STEWARD_DRY_RUN=true` and an
empty allow-list until you've watched it against live data and confirmed alerts
reach you off-box.

---

## Security / public-repo hygiene

- No secrets in the repo. `.gitignore` covers `.env`, `*.db`, build artifacts.
- `scripts/check_secrets.sh` scans for obvious secret patterns; install the
  pre-commit hook with `bash scripts/install-hooks.sh`. CI runs it on every push.
- Optional shared-token auth on the UI/API (`STEWARD_AUTH_TOKEN`).
- The `shell_command` probe is **opt-in** (`STEWARD_ALLOW_SHELL_PROBES=true`).

---

## Project docs

- [`DEPLOY.md`](DEPLOY.md) — deploy to a real Proxmox cluster, safely & in stages.
- [`AGENTIC_SRE_PLAN.md`](AGENTIC_SRE_PLAN.md) — the 3-tier agentic-SRE design.
- [`DEVLOG.md`](DEVLOG.md) — running build log.
- [`ROADMAP.md`](ROADMAP.md) — living backlog (phases + stretch goals).
- [`ASSUMPTIONS.md`](ASSUMPTIONS.md) — every autonomous decision, and why.

## License

[MIT](LICENSE).
