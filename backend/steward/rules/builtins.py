"""Built-in checks shipped with Steward.

These are seeded into the store on first run (and re-seeded if missing). They
are ordinary data — an operator can disable or edit any of them in the UI.
"""
from __future__ import annotations

from steward.checks.schema import Check, ComparisonOp, Condition, ProbeType, SuggestedAction
from steward.models import ActionType, Severity


def builtin_checks() -> list[Check]:
    return [
        Check(
            id="builtin.node_cpu_pressure",
            name="Node CPU pressure",
            probe_type=ProbeType.proxmox_metric,
            target="node:*",
            condition=Condition(metric="cpu_pct", op=ComparisonOp.gt, threshold=85.0),
            severity=Severity.warning,
            cooldown_s=300,
            source="builtin",
            description="Fires when a node's CPU utilisation exceeds 85%.",
            suggested_action=SuggestedAction(
                type=ActionType.migrate,
                params={"strategy": "busiest_vm_to_least_loaded_node"},
                note="Consider migrating the busiest VM off this node.",
            ),
        ),
        Check(
            id="builtin.node_mem_pressure",
            name="Node memory pressure",
            probe_type=ProbeType.proxmox_metric,
            target="node:*",
            condition=Condition(metric="mem_pct", op=ComparisonOp.gt, threshold=90.0),
            severity=Severity.warning,
            cooldown_s=300,
            source="builtin",
            description="Fires when a node's memory utilisation exceeds 90%.",
            suggested_action=SuggestedAction(
                type=ActionType.migrate,
                params={"strategy": "busiest_vm_to_least_loaded_node"},
                note="Memory pressure — migrate a guest or add capacity.",
            ),
        ),
        Check(
            id="builtin.vm_unexpected_stop",
            name="VM unexpectedly stopped",
            probe_type=ProbeType.proxmox_metric,
            target="vm:*",
            condition=Condition(metric="status", op=ComparisonOp.eq, threshold_str="stopped"),
            severity=Severity.critical,
            cooldown_s=600,
            source="builtin",
            description="Fires when a VM/CT is in the stopped state.",
            suggested_action=SuggestedAction(
                type=ActionType.power,
                params={"state": "start"},
                note="Restart the guest if the stop was unintended.",
            ),
        ),
        Check(
            id="builtin.storage_near_full",
            name="Storage near full",
            probe_type=ProbeType.proxmox_metric,
            target="storage:*",
            condition=Condition(metric="used_pct", op=ComparisonOp.gt, threshold=85.0),
            severity=Severity.warning,
            cooldown_s=900,
            source="builtin",
            description="Fires when a storage pool is more than 85% full.",
        ),
        Check(
            id="builtin.cluster_quorum_lost",
            name="Cluster quorum lost",
            probe_type=ProbeType.proxmox_metric,
            target="cluster",
            condition=Condition(metric="quorate", op=ComparisonOp.eq, threshold_str="false"),
            severity=Severity.critical,
            cooldown_s=300,
            source="builtin",
            description="Fires when the cluster loses quorum.",
        ),
    ]
