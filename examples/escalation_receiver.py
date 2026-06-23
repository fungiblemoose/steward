#!/usr/bin/env python3
"""Worked example: the *receiving* side of Steward Tier-2 escalation.

Steward POSTs an incident payload to `STEWARD_ESCALATION_WEBHOOK_URL` when a
check keeps firing on the same target and stays unresolved. This script is a
minimal webhook listener that shows the intended loop:

    incident payload  ->  (investigate)  ->  PROPOSE remediation into the queue

The `propose_from_incident()` function is where the intelligence goes. In a real
deployment you'd hand the payload to a **Claude Code run** — e.g. shell out to
`claude -p "<prompt with the incident + instructions>"` and have it read the
Steward API and decide — then file whatever it proposes. Here it's a small
deterministic stub so the example runs with no dependencies and no LLM.

Crucially, the receiver only ever **proposes** (mode="propose"): remediation
lands in Steward's approval queue and still passes every executor guardrail. It
never executes directly.

Run it:
    export STEWARD_API_URL=http://localhost:8080
    export STEWARD_API_TOKEN=          # if STEWARD_AUTH_TOKEN is set on Steward
    python examples/escalation_receiver.py 9000
    # then point Steward at it:
    #   STEWARD_ESCALATION_WEBHOOK_URL=http://localhost:9000/incident

Stdlib only — no install required.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

API_URL = os.environ.get("STEWARD_API_URL", "http://localhost:8080").rstrip("/")
API_TOKEN = os.environ.get("STEWARD_API_TOKEN", "")


def propose_from_incident(payload: dict) -> Optional[dict]:
    """Decide a remediation to PROPOSE for an incident, or None to just observe.

    This is the seam where a Claude Code run would investigate and decide. The
    stub below encodes a couple of obvious, safe defaults; replace it with a
    `claude -p ...` call that returns an action dict in the same shape.

    Returns an ActionCreate body ({type, params, reason, mode}) or None.
    """
    inc = payload.get("incident", {})
    check_id = inc.get("check_id", "")
    target = inc.get("target", "")

    # A guest that keeps showing up stopped -> propose starting it back up.
    if check_id == "builtin.vm_unexpected_stop":
        try:
            vmid = int(target)
        except (TypeError, ValueError):
            return None
        return {
            "type": "power",
            "params": {"vmid": vmid, "state": "start"},
            "reason": f"escalation: {target} repeatedly stopped — propose restart",
            "mode": "propose",
        }

    # Node pressure is the balancer's job; here we just file a visible note so a
    # human sees the escalation in the queue/audit trail.
    return {
        "type": "notify",
        "params": {
            "title": "Steward escalation",
            "message": f"Unresolved incident: {inc.get('check_name', check_id)} on {target} "
                       f"(x{inc.get('count', '?')}). Investigate.",
            "severity": inc.get("severity", "warning"),
        },
        "reason": f"escalation receiver: surfaced {check_id} on {target}",
        "mode": "propose",
    }


def _api(method: str, path: str, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API_URL}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if API_TOKEN:
        req.add_header("Authorization", f"Bearer {API_TOKEN}")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (trusted local URL)
        return json.loads(resp.read() or "{}")


def handle_incident(payload: dict) -> Optional[dict]:
    """Process one incident: optionally read more context, then file a proposal."""
    # (A real receiver would pull extra context here, e.g. _api("GET", "/api/events").)
    proposal = propose_from_incident(payload)
    if proposal is None:
        return None
    return _api("POST", "/api/actions", proposal)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or "{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            result = handle_incident(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"proposed": result}).encode())
        except Exception as exc:  # noqa: BLE001 - a receiver should answer, then log
            self.send_response(502)
            self.end_headers()
            self.log_message("proposal failed: %s", exc)

    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        sys.stderr.write("[receiver] " + (fmt % args) + "\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"escalation receiver listening on :{port} -> proposing into {API_URL}")
    server.serve_forever()


if __name__ == "__main__":
    main()
