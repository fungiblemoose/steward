# ☀️ Morning summary

Good morning. Steward got built through Phases 1–5 plus several stretch goals,
all on branch `claude/steward-proxmox-babysitter-v8hi5p`, pushed and green.

## TL;DR
A stranger can clone this repo and run the whole thing against a **simulated**
Proxmox cluster: live dashboard, natural-language checks, guarded dry-run
migrations through an approval queue, full audit trail — **zero secrets
required**. 64 backend tests pass; the frontend builds clean.

## What's working (verified live, not just unit tests)
- **Monitoring loop** — deterministic collector polls the mock cluster, persists
  a time-series to SQLite, keeps a ring buffer, evaluates checks, fires events.
  No LLM anywhere in this path.
- **5 built-in checks** — node CPU pressure, node memory pressure, VM
  unexpectedly stopped, storage near full, cluster quorum lost. CRUD-able from
  the UI/API. A check is data, not code.
- **Guarded actions** — one executor enforces, in order: kill switch → dry-run →
  allow-list → cooldown/rate-limit → approval gating → full audit. Migrate /
  power / balloon / notify, with a deterministic migration planner.
- **Approval queue** — CPU pressure suggests a migration; it lands in the queue;
  approving runs it (dry-run by default). With dry-run OFF (safe on the mock) the
  simulator grid actually moves the VM between nodes, fully audited.
- **LLM layer** — pointed at any OpenAI-compatible endpoint (Ollama-ready).
  "alert me if any node memory goes over 85%" → a valid, **disabled**,
  reviewable check. "what's under pressure?" → a grounded answer from live
  metrics. Per-event "Explain". All async, off the loop. Degrades gracefully
  when no endpoint is set. Validated end-to-end against a real mock HTTP server.
- **UI** — dashboard (grid + trend charts + demo load injector), checks manager
  (with JSON import/export), approval queue + audit log, events, chat, logs, and
  a header kill switch + dry-run toggle. Live over WebSocket.
- **Stretch** — history charts, predictive pressure (EWMA forecast warns before
  a threshold), `--demo` scripted incidents, check import/export, log viewer.

## How to run it
```bash
# Option A — Docker (one command)
docker compose up --build         # → http://localhost:8080

# Option B — local dev
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" && python -m pytest && python -m steward
# in another shell:
cd frontend && npm install && npm run dev   # → http://localhost:5173
```
Then on the Dashboard, use **Demo · load injector** to push a VM to 100% CPU and
watch the event fire + a migration land in **Actions → Approval queue**.

Optional LLM: `ollama pull llama3.2:3b`, then set `STEWARD_LLM_BASE_URL=
http://localhost:11434/v1` in `.env`.

## What needs your input
1. **`docker compose up --build` on your machine** — I couldn't run the image
   build here because Docker Hub rate-limited base-image pulls in the sandbox
   (HTTP 429). The dev path is fully verified and the image layout matches it,
   but please confirm the container build on a normal connection. CI will also
   exercise it.
2. **Auth model** — currently a single shared bearer token (fine behind a VPN).
   Want real accounts / RBAC (viewer vs operator)?
3. **Notifications** — shipped noop/ntfy/webhook. Want email/Slack?
4. **Retention** — simple time-based prune today. Want downsampled rollups for
   long-range charts?

## The 3 things I'd do next
1. **Screenshots/GIF in the README** (use `STEWARD_DEMO_MODE=true` to script
   incidents) — the project demos really well and the README should show it.
2. **Migration planner v2** — bin-packing across the whole cluster with the LLM
   narrating the deterministic choice (you already have the planner seam).
3. **A real end-to-end against a lab Proxmox** — the real client is written and
   documented but never exercised; wire a read-only token and validate the
   collector against live data before ever letting it act (keep dry-run ON).

## Repo map
- `backend/steward/` — all backend modules (proxmox, collector via runtime,
  rules, checks, actions, llm, api, store).
- `frontend/src/` — the SPA.
- `DEVLOG.md` — what happened, in order. `ROADMAP.md` — backlog.
  `ASSUMPTIONS.md` — every decision I made while you slept.

Naming note: you said I could rename freely — I kept the product name **Steward**
(it's all over the code/UI) and left the GitHub repo as `watchclawd`. Easy to
change later if you'd rather they match.
