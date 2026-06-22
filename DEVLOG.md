# DEVLOG

Newest entries at the bottom. Times are UTC-ish wall clock from the build host.

---

## 2026-06-22 — Session start: backend through Phase 4

**Context.** Fresh repo (`watchclawd`), building "Steward" per the spec. Public,
build-only, no real infra. Branch: `claude/steward-proxmox-babysitter-v8hi5p`.

**Verified before coding.** Pulled current versions from PyPI rather than trust
memory: FastAPI 0.138, uvicorn 0.49, pydantic 2.13, pydantic-settings 2.14,
httpx 0.28, proxmoxer 2.3, pytest 9.1, websockets 16. Confirmed Proxmox API
shapes for the real client (cluster/resources, nodes/{n}/status, qemu/lxc
migrate/status/config) from the API viewer and encoded them as docs in
`proxmox/real.py`. Host is Python 3.11, Node 22, Docker present.

**Built (in order):**
- `config.py` — env-driven settings; allow-list parses CSV; `llm_enabled` /
  `auth_enabled` helpers.
- `models.py` — Pydantic domain models: node/vm/storage metrics, snapshot,
  events, action request/record + enums. Derived `*_pct` properties.
- `checks/` — the check schema (a check is *data*), comparison ops, conditions,
  target parsing (`node:*`, `vm:101`, `storage:*`, `cluster`), active probes
  (http_get, tcp_port, shell_command [opt-in], process_cpu).
- `proxmox/` — `ProxmoxClient` Protocol; `MockProxmoxClient` simulator with a
  drift model + load injector + mutation (migrate/power/balloon); `RealProxmoxClient`
  (gated, untested, documented); factory defaults to mock.
- `store/db.py` — stdlib sqlite3, WAL, single `metrics` time-series table +
  checks/events/actions/app_state, retention prune, audit helpers.
- `rules/` — pure `evaluate_check` + `RuleEngine` with per-(check,target)
  cooldown; 5 built-in checks.
- `actions/` — guarded executor (kill switch → dry-run → allow-list → cooldown/
  rate-limit → approval → audit), deterministic migration planner.
- `notify.py` — noop / ntfy / webhook senders.
- `llm/` — OpenAI-compatible httpx client + fake; NL→check, grounded Q&A,
  explain. Off the hot path; degrades gracefully when unconfigured.
- `runtime.py` — service container + collector loop (poll → store → ring buffer
  → evaluate → events → notify → suggest → broadcast → periodic prune).
- `api/` — FastAPI REST + WebSocket; auth dep (shared token); serves built SPA.

**Key decisions** (full list in ASSUMPTIONS.md): stdlib sqlite over an ORM;
httpx over the openai SDK; allow-list governs the auto path only (human approval
overrides); LLM checks created disabled; server owns check id/source/enabled.

**Tested.** 52 pytest tests green: rule engine (each built-in, cooldown,
disabled), simulator (migrate/power/balloon/load injection/protocol conformance),
guardrails (dry-run, real, kill switch, allow-list, human override, cooldown,
rate limit, propose/approve/reject, planner, audit), store CRUD + retention,
runtime collector (event fires + suggestion queued, nothing auto-executes),
API (health/state/checks CRUD/flags/actions/auth/demo), LLM (nl→check disabled,
garbage rejected, grounded Q&A, explain).

**Live smoke test.** Booted uvicorn against the mock: `/api/health` ok, dashboard
state shows the 3 nodes, injecting load on pve-1's VMs fires a "Node CPU
pressure" event and lands a migration suggestion in the approval queue. Works.

**Punted to later tonight:** frontend SPA, Dockerfile/compose, README, in-UI log
viewer, charts. Picking those up next.

**Open questions for you:** RBAC vs single shared token? Extra notification
channels? Downsampled rollups for long-term charts? (All in ASSUMPTIONS.md.)
