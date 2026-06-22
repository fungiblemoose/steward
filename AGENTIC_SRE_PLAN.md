# Steward → Agentic Homelab SRE — Design & Plan

> Status: **proposed**. This is the plan, not the build. Tiers 0 and 2 are
> scoped for implementation; Tier 1 is deliberately deferred (see
> [Hardware reality](#hardware-reality)).

## What we're actually building

A **local, mostly-autonomous homelab SRE** for a Proxmox cluster. It should:

1. Watch the cluster continuously and **act on its own** to keep it healthy —
   chiefly by **live-migrating VMs/CTs to balance resource use**.
2. **Investigate** problems by going *into* containers (tail logs, hit an app's
   API, check a process) rather than only reading host metrics — eventually
   agentically.
3. Only **page a human (Claude Code)** for the genuinely hard cases it can't
   resolve itself.

This is the role you currently use Claude Code for, made resident and local so
routine operations don't run up a metered bill.

## The core principle (the one that saves the token bill)

**An LLM in a loop is exactly the thing that costs money.** So the design is not
"make the agent local" — it's **make the agent barely run at all**:

- The routine 95% — polling, threshold evaluation, *and the migration
  balancing itself* — is **deterministic code with no model in the path**. It
  runs 24/7 for free.
- A model is invoked only for the fuzzy/hard 5%: explaining an incident,
  diagnosing something a threshold can't express, or proposing a novel fix.
- The most capable model (Claude) is paged **rarely**, only when the local tiers
  can't resolve an incident. Rare escalation is what keeps it cheap.

Steward's existing architecture already embodies this — the LLM is off the
collector loop and never mutates. This plan extends that spine; it does not
rebuild it.

## Three tiers

| Tier | What | Where | Model? | Cost | Status |
|------|------|-------|--------|------|--------|
| **0** | Deterministic monitor **+ autonomous migration balancer** | LXC, 24/7 | none | free | **build now** |
| **1** | Local agentic investigator (execs into containers) | Mac Mini (MLX) | small local | on-incident | **deferred** |
| **2** | Escalate unresolved incidents to Claude Code | webhook → existing remote-agent infra | Claude (cloud) | rare | **build now** |

The order matters: Tier 0 does the work, Tier 2 is the safety net, and Tier 1
is the nice-to-have that depends on hardware we don't have yet. Building 0 and 2
first gives most of the value and keeps the seams open for 1.

## Scope of THIS plan

**In:** Tier 0 (autonomous balancer) and Tier 2 (Claude escalation).
**Stubbed, not built:** Tier 1 (local agentic investigator) — seams preserved so
it drops in later without rework.

---

## Tier 0 — Autonomous live-migration balancer (deterministic, free)

### The good news: the execution path already exists

The autonomous-action machinery is already in place. `runtime.py` already does:

```python
if check.auto_execute:
    await self.executor.run(req)          # auto path
else:
    await asyncio.to_thread(self.executor.propose, req)   # approval queue
```

and the executor enforces the full guardrail chain (kill-switch → allow-list →
cooldown/rate-limit → dry-run → audit). So Tier 0 is **not** a new execution
path. It is two new deterministic pieces feeding the *existing* one:

1. **A cluster-imbalance metric** the rule engine can threshold on.
2. **A proactive balancing planner** that emits `(vmid, target)` moves.

The current `actions/planner.py::plan_migration_target()` is *reactive* (given a
vmid, find a target). We add a *proactive* function that decides **which** guests
to move and where.

### New pieces (grounded in the current code)

| New piece | Lives in | Does |
|---|---|---|
| `cluster_imbalance(snap, dim) -> float` | new `balancer/` module | stddev of online-node CPU/mem load; `0.0` = balanced |
| `suggest_balancing_migrations(snap, dim, …) -> list[(vmid, target)]` | `balancer/` | greedy: busiest guest on the most-overloaded node → least-loaded eligible target; capped at `max_concurrent` |
| `migration_impact(snap, vmid, target)` | `actions/planner.py` | simulate the move; refuse if it doesn't reduce imbalance by more than a margin, or if `mem/bandwidth` cost outweighs benefit |
| `builtin.autonomous_balancer` check | `rules/builtins.py` | `target="cluster"`, fires when `imbalance_cpu > threshold`; `auto_execute=True` |
| `imbalance_cpu` / `imbalance_mem` derived fields | `models.py::ClusterSnapshot` | computed each poll so the rule engine can threshold them |
| `count_in_flight(type) -> int` | `store/db.py` | enforce a max-concurrent-migrations cap (rate limit alone isn't enough) |
| realistic migration delay | `proxmox/mock.py` | so tests can prove the concurrency cap actually holds (real migrations take 10–120s; the mock is currently instant) |

### Algorithm (deterministic, testable)

1. Each poll, compute `imbalance_cpu/mem` (stddev of online-node loads) and
   stash on the snapshot.
2. The `autonomous_balancer` check fires when imbalance exceeds a threshold *and*
   has been trending up over the ring buffer (don't shuffle a momentarily-spiky
   but stable cluster).
3. `suggest_balancing_migrations` picks the busiest movable guest on the hottest
   node and the best eligible target (online, has headroom, not the source).
4. Each candidate runs through `migration_impact`; reject moves whose benefit <
   margin or whose copy-cost (`mem / ~1000 MB·s⁻¹`) outweighs the gain.
5. Survivors go to `executor.run()` and pass the existing guardrails. A live
   re-validation (target still online + has headroom) happens immediately
   before `_perform()`.

### Autonomy posture — full autonomy *within guardrails*, with a safe ramp

You chose **full autonomy within guardrails**. The balancer therefore ships
*capable* of acting unattended, but autonomy is **opt-in per guest and gated by
the same switches that already exist**, so "full autonomy" can't mean "surprise
live-migrations on day one":

- The balancer check is `auto_execute=True`, **but** a guest only auto-moves if
  it is on the **allow-list** (empty by default) — and **`dry_run` defaults ON**.
- Recommended ramp (encoded as docs, not enforced): run with `dry_run=true` for
  a burn-in, watch the audit log show what it *would* do, then allow-list a few
  low-stakes CTs and flip `dry_run=false`. Graduate from there.
- **Exclude HA-managed guests** from the allow-list so Steward and the Proxmox
  HA manager don't fight (the real client documents this; the allow-list
  enforces it).

This honors "hands-off" while making the first unattended migration a deliberate
act, not an accident — the right default for live-migrating real workloads.

### Milestones (Tier 0)

- [ ] `cluster_imbalance` + derived snapshot fields + unit tests
- [ ] `suggest_balancing_migrations` + `migration_impact` (pure, fully tested)
- [ ] `builtin.autonomous_balancer` check (`auto_execute=True`, off until allow-listed)
- [ ] max-concurrent-migrations guardrail (`store.count_in_flight`) + pre-execute live re-validation
- [ ] mock migration delay so concurrency is provable in tests
- [ ] `/api/balancer/simulate` — show the moves it *would* make without executing
- [ ] dashboard: imbalance gauge + "what the balancer is thinking"

---

## Tier 2 — Escalate to Claude Code on unresolved incidents

### Why it's cheap

Escalation fires only when the deterministic tiers can't resolve an incident —
so Claude runs on the rare hard case, not on a polling cadence. That is the
entire point: **replace the constant pinging, not the intelligence.**

### Design

The notifier layer is already pluggable (`notify.py`: `none | ntfy | webhook`).
Escalation is a new notifier kind plus a thin incident concept:

- **Incident model** — today every `Event` is sent independently. Add light
  dedup/aging so an *incident* = repeated warning/critical events from the same
  `(check_id, target)` that **stay unresolved past N occurrences / M minutes**.
  Only incidents escalate; transient blips don't.
- **`ClaudeEscalateNotifier`** (`STEWARD_NOTIFY_KIND=claude_escalate`) — POSTs an
  incident payload (the `Event`, recent matching events, the current
  `ClusterSnapshot`, and links to the relevant checks/actions) to a webhook.
- **Trigger path** — the webhook kicks off a **Claude Code run on your existing
  remote-agent / cron infrastructure** (the same machinery behind the daily
  C-suite briefs). Claude investigates through Steward's read API, then either
  posts an explanation back or *proposes* actions into the existing approval
  queue (it never bypasses guardrails).

### Milestones (Tier 2)

- [ ] Incident dedup/aging in the store (escalate only unresolved, repeated events)
- [ ] `ClaudeEscalateNotifier` + config flags + factory wiring
- [ ] Incident payload schema (event + history + snapshot + links)
- [ ] Webhook → remote Claude Code agent that reads the API and proposes (not executes)
- [ ] Audit: every escalation and every agent-proposed action is a row

---

## Tier 1 — Local agentic investigator (DEFERRED)

The "go *into* the container and figure out what's wrong" piece. Deferred, with
its seams preserved so it's a drop-in later.

### Why deferred — hardware

A useful agentic investigator needs a competent tool-using model (≥7–14B). The
intended host is the **M4 Mac Mini (16 GB unified memory)**, which already runs
**Compel (~9 GB when active)**. After macOS overhead there isn't room for even a
7B model alongside Compel. **Tier 1 waits for more memory** (a bigger Mini, a
dedicated model box, or the planned NAS/inference host). Forcing it onto current
hardware would mean a tiny model that flails — worse than not having it.

### Seams we preserve *now* so it drops in cleanly

- **Container-exec tool = the `shell_command` probe pattern.** A future
  `container_exec` probe/tool reuses the exact opt-in gate
  (`STEWARD_ALLOW_CONTAINER_EXEC=true`, mirroring `STEWARD_ALLOW_SHELL_PROBES`),
  the timeout, and the `Event`/audit path. It runs `pct exec <ctid> -- <cmd>`
  **read-only by default**; anything mutating routes through the action executor
  and approval queue — same guardrails as migration.
- **Agent brain = the LLM client seam.** The investigator is a bounded ReAct
  loop built on the existing `llm/client.py` protocol; pointing it at a local
  MLX/Ollama endpoint is a config change, not a rewrite.
- **Tight, mostly read-only toolbelt:** metric queries, log tail, app-API GET,
  `pct exec` of allow-listed read commands. Open-ended shell is *not* on the
  menu — scope is what keeps a small model safe and useful.

When the hardware exists, Tier 1 slots between 0 and 2: try the local agent
first, escalate to Claude only if it's stuck.

---

## Hardware reality

- **Cluster is 2 nodes** (Beelink N150s). Live migration between them is real,
  but balancing headroom is thin — if the cluster is simply *full*, the balancer
  can't conjure capacity. Tier 0 is a real-but-occasional win, not magic. The
  imbalance threshold + cost gate keep it from thrashing two small nodes.
- **Out-of-band alerting.** An in-cluster LXC dies with the cluster. Tier 0 can
  live in an LXC, but the **escalation/notification path (ntfy + Tier 2) should
  survive a cluster outage** — that's the one thing that has to phone home when
  everything else is down.
- **16 GB Mac Mini + Compel** is the binding constraint on Tier 1 (above).

## Risks & how they're contained

- **Live-migrating real workloads unattended** → dry-run default ON, allow-list
  opt-in, HA-managed guests excluded, max-concurrent cap, pre-execute target
  re-validation, full audit, global kill switch.
- **Balancer thrash** (oscillating migrations) → imbalance threshold + upward-
  trend gate + cooldown + cost-vs-benefit margin.
- **Container exec = arbitrary code execution** (Tier 1) → off by default,
  read-only default, allow-listed commands, mutations through the executor.
- **Escalation spam / cost** → incident dedup/aging; only unresolved, repeated
  incidents page Claude.

## Open questions

- Balancer dimension priority — balance on **CPU**, **memory**, or a weighted
  blend? (Default: CPU, with memory as a hard headroom constraint on targets.)
- Escalation trigger threshold — how many repeats / how long unresolved before
  paging Claude? (Default proposal: ≥3 occurrences over ≥10 min, still firing.)
- Where the Tier-0 LXC lives — pve vs pve2 — and whether to also run a tiny
  out-of-band heartbeat on pi24 so "the cluster is down" can still alert.
