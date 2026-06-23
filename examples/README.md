# Examples

## `escalation_receiver.py` — the receiving side of Tier-2 escalation

A minimal, dependency-free webhook listener that demonstrates the intended
Tier-2 loop: receive an incident from Steward → investigate → **propose**
remediation into Steward's approval queue (never execute directly).

```bash
export STEWARD_API_URL=http://localhost:8080
export STEWARD_API_TOKEN=          # only if STEWARD_AUTH_TOKEN is set on Steward
python examples/escalation_receiver.py 9000
```

Then point Steward at it:

```bash
# in Steward's .env
STEWARD_ESCALATION_WEBHOOK_URL=http://localhost:9000/incident
```

Now when a check fires repeatedly on the same target and stays unresolved,
Steward POSTs the incident here, and the receiver files a proposed action you'll
see in **Actions → Approval queue**.

### Making it actually agentic

`propose_from_incident(payload)` is the seam. The stub encodes two safe defaults
(restart a guest that keeps showing up stopped; otherwise file a visible note).
Replace it with a **Claude Code run** that reads the live state and decides — for
example, shell out to the `claude` CLI with the incident as context:

```python
import subprocess, json
def propose_from_incident(payload):
    prompt = (
        "You are an SRE. Here is an unresolved Proxmox incident:\n"
        f"{json.dumps(payload, indent=2)}\n"
        "Investigate via the Steward API and return ONE action to PROPOSE as JSON "
        '{type, params, reason} — or null. Never execute; propose only.'
    )
    out = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True).stdout
    action = json.loads(out)
    return {**action, "mode": "propose"} if action else None
```

The receiver always uses `mode="propose"`, so whatever the agent decides still
passes every executor guardrail (allow-list, dry-run, cooldown, audit) before
anything mutates.
