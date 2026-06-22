import React, { useEffect, useState } from "react";
import { fetchBalancerSimulation } from "../api";

// Status pill colors keyed by the balancer's three states.
function statusPill(enabled, wouldAct) {
  if (!enabled) {
    return { label: "disabled", cls: "text-gray-400 bg-gray-900 border-gray-700" };
  }
  if (wouldAct) {
    return { label: "rebalancing", cls: "text-amber-300 bg-amber-950 border-amber-800" };
  }
  return { label: "balanced", cls: "text-emerald-300 bg-emerald-950 border-emerald-800" };
}

function Gauge({ value, threshold, over }) {
  // Fill the bar relative to a scale that always shows the threshold marker.
  // Cap the visual scale a bit above the larger of value/threshold so an
  // over-threshold value still reads clearly.
  const scale = Math.max(threshold, value, 1) * 1.25;
  const fillPct = Math.min(100, (value / scale) * 100);
  const markerPct = Math.min(100, (threshold / scale) * 100);
  const barColor = over ? (value >= threshold * 1.5 ? "bg-red-500" : "bg-amber-500") : "bg-emerald-500";

  return (
    <div className="relative h-3 rounded bg-gray-800 overflow-hidden">
      <div className={`h-full ${barColor}`} style={{ width: `${fillPct}%` }} />
      {/* threshold marker */}
      <div
        className="absolute top-0 bottom-0 w-px bg-gray-300"
        style={{ left: `${markerPct}%` }}
        title={`threshold ${threshold}`}
      />
    </div>
  );
}

export default function Balance() {
  const [sim, setSim] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const data = await fetchBalancerSimulation();
        if (!active) return;
        setSim(data);
        setError("");
      } catch (e) {
        if (!active) return;
        setError(String(e.message || e));
      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    const id = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  if (loading && !sim) {
    return (
      <div className="rounded-lg border border-gray-800 bg-[#0d1320] p-4 text-gray-600 text-sm">
        loading balancer…
      </div>
    );
  }

  if (error && !sim) {
    return (
      <div className="rounded-lg border border-gray-800 bg-[#0d1320] p-4 text-red-400 text-sm">
        Balancer unavailable — {error}
      </div>
    );
  }

  const enabled = !!sim?.enabled;
  const wouldAct = !!sim?.would_act;
  const blended = sim?.blended_imbalance ?? 0;
  const threshold = sim?.threshold ?? 0;
  const cpu = sim?.imbalance_cpu;
  const mem = sim?.imbalance_mem;
  const weights = sim?.weights || {};
  const moves = sim?.moves || [];
  const over = enabled && blended > threshold;
  const pill = statusPill(enabled, wouldAct);

  return (
    <div className="rounded-lg border border-gray-800 bg-[#0d1320] p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold tabular-nums">{blended.toFixed(1)}</span>
          <span className="text-xs text-gray-500">
            blended imbalance · threshold {threshold.toFixed(1)}
          </span>
        </div>
        <span className={`text-[10px] px-2 py-0.5 rounded border ${pill.cls}`}>{pill.label}</span>
      </div>

      <Gauge value={blended} threshold={threshold} over={over} />

      <div className="flex gap-6 text-xs">
        <div>
          <span className="text-gray-500">CPU </span>
          <span className="tabular-nums">{cpu != null ? cpu.toFixed(1) : "—"}</span>
          {weights.cpu != null && <span className="text-gray-600"> ×{weights.cpu}</span>}
        </div>
        <div>
          <span className="text-gray-500">Mem </span>
          <span className="tabular-nums">{mem != null ? mem.toFixed(1) : "—"}</span>
          {weights.mem != null && <span className="text-gray-600"> ×{weights.mem}</span>}
        </div>
      </div>

      <div>
        <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">
          Preview moves (dry run)
        </div>
        {moves.length === 0 ? (
          <div className="text-xs text-gray-500">No moves needed.</div>
        ) : (
          <div className="space-y-1">
            {moves.map((m) => (
              <div
                key={m.vmid}
                className="flex items-center justify-between text-xs font-mono rounded border border-gray-800 bg-gray-900/40 px-2 py-1"
              >
                <span>
                  <span className="font-medium">{m.name}</span>
                  <span className="text-gray-500"> ({m.vmid})</span>
                </span>
                <span className="text-gray-400">
                  {m.source} <span className="text-sky-400">→</span> {m.target}
                </span>
                <span className="text-emerald-400 tabular-nums">
                  −{(m.improvement ?? 0).toFixed(1)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
