import React, { useEffect, useState } from "react";
import { fetchDiff } from "../api";
import { statusBadge } from "../util";

// Window options for the trailing diff. Mirrors the since_s contract.
const WINDOWS = [
  { label: "5m", sinceS: 300 },
  { label: "15m", sinceS: 900 },
  { label: "1h", sinceS: 3600 },
];

// Human-readable span. util.js has no duration helper, so format inline.
function fmtSpan(s) {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(s % 3600 ? 1 : 0)}h`;
}

// Load deltas: a drop in load (negative) is good → green; a rise is red.
function Delta({ value }) {
  if (value == null) return <span className="text-gray-600 tabular-nums">—</span>;
  const up = value > 0;
  const flat = Math.abs(value) < 0.05;
  const cls = flat ? "text-gray-400" : up ? "text-red-400" : "text-emerald-400";
  const arrow = flat ? "·" : up ? "↑" : "↓";
  return (
    <span className={`tabular-nums ${cls}`}>
      {arrow} {value > 0 ? "+" : ""}
      {value.toFixed(1)}
    </span>
  );
}

function StatusTransition({ from, to }) {
  return (
    <span className="inline-flex items-center gap-1 text-[10px]">
      <span className={`px-1.5 py-0.5 rounded border ${statusBadge(from)}`}>{from}</span>
      <span className="text-gray-500">→</span>
      <span className={`px-1.5 py-0.5 rounded border ${statusBadge(to)}`}>{to}</span>
    </span>
  );
}

// Pick the right badge for a guest change row.
function vmBadge(v) {
  if (v.change === "appeared") {
    return { label: "appeared", cls: "text-emerald-300 bg-emerald-950 border-emerald-800" };
  }
  if (v.change === "disappeared") {
    return { label: "disappeared", cls: "text-red-300 bg-red-950 border-red-800" };
  }
  if (v.moved) {
    return {
      label: `migrated ${v.moved.from} → ${v.moved.to}`,
      cls: "text-sky-300 bg-sky-950 border-sky-800",
    };
  }
  if (v.status) {
    const started = v.status.to === "running" || v.status.to === "online";
    return {
      label: started ? "started" : "stopped",
      cls: started
        ? "text-emerald-300 bg-emerald-950 border-emerald-800"
        : "text-amber-300 bg-amber-950 border-amber-800",
    };
  }
  return null;
}

export default function Changes() {
  const [sinceS, setSinceS] = useState(300);
  const [diff, setDiff] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    async function load() {
      try {
        const data = await fetchDiff(sinceS);
        if (!active) return;
        setDiff(data);
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
  }, [sinceS]);

  const nodes = diff?.nodes || [];
  const vms = diff?.vms || [];

  const windowPicker = (
    <div className="flex gap-1">
      {WINDOWS.map((w) => (
        <button
          key={w.sinceS}
          onClick={() => setSinceS(w.sinceS)}
          className={`text-[11px] px-2 py-0.5 rounded border transition ${
            sinceS === w.sinceS
              ? "border-sky-600 text-sky-300 bg-sky-950"
              : "border-gray-700 text-gray-400 hover:border-gray-600"
          }`}
        >
          {w.label}
        </button>
      ))}
    </div>
  );

  if (loading && !diff) {
    return (
      <div className="rounded-lg border border-gray-800 bg-[#0d1320] p-4 text-gray-600 text-sm">
        loading recent changes…
      </div>
    );
  }

  if (error && !diff) {
    return (
      <div className="rounded-lg border border-gray-800 bg-[#0d1320] p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">over the last</span>
          {windowPicker}
        </div>
        <div className="text-red-400 text-sm">Changes unavailable — {error}</div>
      </div>
    );
  }

  const empty = nodes.length === 0 && vms.length === 0;

  return (
    <div className="rounded-lg border border-gray-800 bg-[#0d1320] p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">
          over the last <span className="text-gray-300">{fmtSpan(diff?.span_s)}</span>
        </span>
        {windowPicker}
      </div>

      {empty ? (
        <div className="text-xs text-gray-500">No notable changes.</div>
      ) : (
        <div className="space-y-3">
          {nodes.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Nodes</div>
              <div className="space-y-1">
                {nodes.map((n) => (
                  <div
                    key={n.node}
                    className="flex items-center justify-between gap-3 text-xs rounded border border-gray-800 bg-gray-900/40 px-2 py-1"
                  >
                    <span className="font-medium">{n.node}</span>
                    <div className="flex items-center gap-3">
                      {n.status && <StatusTransition from={n.status.from} to={n.status.to} />}
                      <span className="flex gap-1">
                        <span className="text-gray-600">cpu</span>
                        <Delta value={n.cpu_delta} />
                      </span>
                      <span className="flex gap-1">
                        <span className="text-gray-600">mem</span>
                        <Delta value={n.mem_delta} />
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {vms.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Guests</div>
              <div className="space-y-1">
                {vms.map((v) => {
                  const badge = vmBadge(v);
                  return (
                    <div
                      key={v.vmid}
                      className="flex items-center justify-between gap-3 text-xs rounded border border-gray-800 bg-gray-900/40 px-2 py-1"
                    >
                      <span>
                        <span className="font-medium">{v.name}</span>
                        <span className="text-gray-500"> ({v.vmid})</span>
                        <span className="text-gray-600"> · {v.node}</span>
                      </span>
                      <div className="flex items-center gap-3">
                        {v.cpu_delta != null && (
                          <span className="flex gap-1">
                            <span className="text-gray-600">cpu</span>
                            <Delta value={v.cpu_delta} />
                          </span>
                        )}
                        {badge && (
                          <span className={`text-[10px] px-2 py-0.5 rounded border ${badge.cls}`}>
                            {badge.label}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
