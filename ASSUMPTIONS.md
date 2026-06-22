# Assumptions

Decisions made autonomously where the spec left room. Each is reversible.

## Environment / stack
- **Python 3.11**, not 3.12. The build host ships 3.11.15; all code is written
  to be 3.11-compatible (`requires-python = ">=3.11"`). CI runs on 3.12 to keep
  the door open. Nothing used is 3.12-only.
- **SQLite via stdlib `sqlite3`**, no ORM. Full control over the time-series
  schema, retention, and rollups with a tiny dependency surface. WAL mode +
  a process-wide lock makes it safe across FastAPI's threadpool and the
  collector (which calls the store via `asyncio.to_thread`).
- **LLM over raw `httpx`** against `POST {base_url}/chat/completions` instead of
  the `openai` SDK. Identical behaviour against Ollama/llama.cpp/vLLM/OpenAI,
  fewer dependencies, and a trivially fakeable client for tests.
- **Frontend: React + Vite + Tailwind** (per spec), built in a multi-stage
  Docker image and served by FastAPI from `frontend/dist`. Dev uses the Vite
  proxy to the backend.

## Behaviour
- **Allow-list governs the AUTO path only.** A human approving a queued action
  is treated as explicit consent and may act on a non-allow-listed guest. The
  allow-list still blocks rule/LLM-initiated auto-actions on anything not listed.
  Rationale: the allow-list exists to stop *unattended* changes; a human in the
  loop is the stronger gate. (See `actions/executor.py::_run`.)
- **LLM-generated checks are created DISABLED** with `source="llm"`. That is the
  "approval queue for checks": they appear in the UI for review and must be
  toggled on by a human before they can ever fire. Even once enabled, any action
  they suggest still flows through the action approval queue.
- **Server owns identity fields.** When the LLM returns a check, the server
  overwrites `id`, `source`, and `enabled` — the model cannot self-enable a
  check or spoof provenance.
- **Default cluster fixture**: a generic 3-node cluster (`pve-1..3`) with a
  shared `ceph-pool` and per-node `local` storage, plus 8 mixed VMs/CTs. Two
  busy VMs on `pve-1` are given 6 vCPUs each (overcommit) so the node can
  actually be driven past the CPU-pressure threshold for demos/tests — real
  clusters overcommit routinely.
- **`shell_command` probe is opt-in** (`STEWARD_ALLOW_SHELL_PROBES=true`).
  Running arbitrary shell from a monitoring daemon is a foot-gun; off by default.
- **`process_cpu` probe** is evaluated against the snapshot (a guest's CPU by
  vmid/name) rather than reaching into a real process table, since there is no
  real infra tonight.

## Safety posture (unchanged from spec, restated)
- `dry_run=true` and `proxmox_mode=mock` are the defaults. Nothing contacts real
  infrastructure. The real Proxmox client exists and is wired but is never
  constructed unless explicitly configured, and `proxmoxer` is an optional dep.

## Agentic-SRE direction (proposed — see `AGENTIC_SRE_PLAN.md`)
- **Tiered by design: deterministic does the routine work; an LLM is rare.** The
  Tier-0 migration *balancer* is pure deterministic code (no model in the path).
  A model is only invoked for the hard 5% (Tier 2 escalation now; a local Tier-1
  investigator later). Rationale: an LLM in a loop is what costs tokens — the win
  is keeping it out of the loop, not making the loop local.
- **"Full autonomy within guardrails" still defaults to dry-run + empty
  allow-list.** Operator chose hands-off autonomy; the balancer ships
  `auto_execute=True` but a guest only auto-moves once it's allow-listed and
  `dry_run` is off. Recommended ramp: dry-run burn-in → allow-list low-stakes CTs
  → flip dry-run. So the first unattended live-migration is deliberate, not a
  surprise. HA-managed guests stay off the allow-list (don't fight the HA manager).
- **Tier 1 (local agentic investigator) is deferred on hardware grounds.** The
  16 GB Mac Mini already runs Compel (~9 GB); no room for a capable agent model.
  We build the *seams* now (container-exec mirrors the `shell_command` opt-in
  gate; the brain reuses the LLM client protocol) and slot the agent in when a
  bigger inference host exists.
- **Escalation reuses the notifier seam.** Claude-Code escalation is a new
  notifier kind, fired only on deduped, unresolved, repeated incidents; the agent
  reads via the API and *proposes* into the approval queue — it never bypasses the
  executor guardrails.

## Open questions for the morning
- Auth is a single shared bearer token (good enough for a homelab behind a VPN).
  Do you want real user accounts / RBAC (viewer vs operator)? Stubbed in ROADMAP.
- Notification channels shipped: ntfy + generic webhook + noop. Want email/Slack?
- Retention is a simple time-based prune. Want downsampled rollups for long-term
  charts (e.g. 1-min raw → 5-min/1-hour aggregates)?
- Where the Tier-0 LXC lives — pve vs pve2 — and whether to run a tiny
  out-of-band heartbeat on pi24 so a cluster outage can still phone home.

## Agentic-SRE decisions (locked)
- **Balancer dimension = blended CPU+mem** (weighted, default 0.5/0.5,
  configurable), with **memory as a hard headroom constraint** on candidate
  targets — never migrate onto a node short on RAM, however good the CPU math.
- **Escalation trigger = ≥3 occurrences over ≥10 min, still firing.** Only
  deduped, unresolved, repeated incidents page Claude.
