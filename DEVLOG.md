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

---

## 2026-06-22 — Frontend, infra, hardening, stretch goals

**Built the rest of the stack and pushed past the MVP.**

- **Frontend SPA** (React 19 / Vite 8 / Tailwind 4): dashboard (live node/VM
  grid, color-coded pressure bars, per-node CPU/mem trend chart via recharts,
  demo load injector), checks manager (NL→check, toggle/delete, JSON
  import/export), approval queue + audit log, events with LLM "Explain",
  chat panel, and a Logs tab. Header has the global kill switch + dry-run
  toggle + LLM/mode badges + token field. Live via WebSocket with auto-reconnect.
- **Packaging:** multi-stage Dockerfile (build SPA → slim non-root python
  runtime, healthcheck) and a compose file that boots the mock demo with no
  secrets. `.dockerignore` keeps the context lean.
- **Hygiene:** `scripts/check_secrets.sh` (staged + tree modes), install-hooks
  script, and a GitHub Actions CI (secret scan + backend pytest + frontend
  build). Verified the scanner both passes clean and catches a planted key.
- **Stretch goals landed:** per-node history charts; **predictive pressure**
  (deterministic EWMA + least-squares forecast → "projected to exceed X% in
  ~N min" info events, off the LLM path); check **import/export** as JSON;
  **`--demo` mode** with scripted incidents; in-UI **log viewer**.
- **Audit fidelity fix:** real (non-dry) runs now re-read fresh client state for
  the audit `after`, instead of the cached poll snapshot.

**Verified live, not just unit-tested:**
- SPA + assets + SPA-fallback served by FastAPI from `frontend/dist`.
- Full action flow: proposed migration → approved → executed; with dry-run OFF
  (safe on the mock) the simulator grid actually moves VM 101 pve-1 → pve-3,
  fully audited. With dry-run ON nothing mutates and the audit shows the
  simulated outcome.
- **LLM end-to-end against a real OpenAI-compatible HTTP server** (a tiny mock):
  `/api/llm/status` enabled, NL→check returned a valid disabled `source=llm`
  check, grounded Q&A answered. No LLM call sits in the collector loop.

**Tests:** 64 passing (rules, simulator, guardrails, store, runtime, API, LLM,
predictive). Frontend builds clean.

**Could NOT verify tonight:** the actual `docker build` — Docker Hub
rate-limited unauthenticated base-image pulls in the build sandbox (429). The
Dockerfile layout is verified to match what the app serves (same `frontend/dist`
relative path the dev run uses), and CI will exercise it on a runner with normal
pull limits. **Try `docker compose up --build` on your machine to confirm.**

**Still open / nice-to-have:** screenshots/gif in the README; coverage report in
CI; "what changed?" diff view; migration planner v2 (bin-packing); RBAC. All in
ROADMAP.

---

## 2026-06-22 — Phase 6: Tier-0 autonomous load balancer

**Context.** First build off the `AGENTIC_SRE_PLAN.md` design (PR #3). Goal:
deterministic, no-LLM live-migration balancing that runs on the poll loop and
reuses the existing guarded executor. Branch: `feat/tier0-balancer`.

**Built.**
- `balancer/policy.py` — pure scoring + planning. `imbalance()` is the stddev of
  online-node load on a dimension; `blended_imbalance()` weights CPU+mem;
  `suggest_balancing_migrations()` greedily trial-migrates every running guest
  onto every eligible target and keeps the move that most reduces blended
  imbalance (subject to a min-improvement floor and a CPU+mem headroom cap, with
  memory a *hard* constraint). `trending_up()` gates out receding spikes.
- `builtin.autonomous_balancer` check — `target="cluster"`, `auto_execute=True`,
  but ships **disabled**. Enabling it is the deliberate on-switch; even then a
  guest only moves if allow-listed and dry-run is off.
- `runtime._run_balancer()` — threshold + trend + per-balancer-cooldown +
  migration-settle gating, emits one audit event, routes each move through
  `executor.run()`. Kept off the `_maybe_suggest` path so it can't double-fire.
- Executor: pre-execute live re-validation (refuse a target that isn't online
  right now).
- `NodeMetric.cpu_cores` added (mock + real populate it) so a move's CPU effect
  on a node is simulable; memory already moves with the guest exactly.
- Config knobs under `STEWARD_BALANCER_*`; documented in `.env.example`.

**Decisions locked earlier (PR #3):** balance on a blended CPU+mem score
(default 0.5/0.5); memory is a hard target-headroom constraint.

**Tests.** 76 passing (was 64): new `test_balancer.py` covers scoring, trend,
move simulation, and the memory/CPU caps; `test_runtime.py` gains an
enable→imbalance→dry-run-migrate integration test and asserts the balancer stays
dormant while its check is disabled.

**Still open (Phase 6 tail):** `/api/balancer/simulate` endpoint + a dashboard
imbalance gauge so the operator can preview moves before enabling.
