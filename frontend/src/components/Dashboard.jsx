import React, { useEffect, useState } from "react";
import { api } from "../api";
import { pctColor, statusBadge } from "../util";
import NodeChart from "./NodeChart";
import Balance from "./Balance";
import Changes from "./Changes";

function Bar({ label, pct }) {
  return (
    <div className="mb-1.5">
      <div className="flex justify-between text-[11px] text-gray-400">
        <span>{label}</span>
        <span>{pct != null ? `${pct.toFixed(0)}%` : "—"}</span>
      </div>
      <div className="h-2 rounded bg-gray-800 overflow-hidden">
        <div className={`h-full ${pctColor(pct)}`} style={{ width: `${Math.min(100, pct || 0)}%` }} />
      </div>
    </div>
  );
}

export default function Dashboard({ snapshot }) {
  const [chartNode, setChartNode] = useState(null);
  const nodes = snapshot?.nodes || [];
  const vms = snapshot?.vms || [];

  useEffect(() => {
    if (!chartNode && nodes.length) setChartNode(nodes[0].node);
  }, [nodes, chartNode]);

  if (!snapshot) {
    return <div className="p-6 text-gray-500">Waiting for first poll…</div>;
  }

  return (
    <div className="p-5 space-y-6">
      <section>
        <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">
          Nodes {snapshot.quorate ? "" : "· ⚠ QUORUM LOST"}
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {nodes.map((n) => (
            <div
              key={n.node}
              onClick={() => setChartNode(n.node)}
              className={`rounded-lg border p-3 cursor-pointer transition ${
                chartNode === n.node ? "border-sky-600" : "border-gray-800"
              } bg-[#0d1320] hover:border-gray-600`}
            >
              <div className="flex justify-between items-center mb-2">
                <span className="font-medium">{n.node}</span>
                <span className={`text-[10px] px-2 py-0.5 rounded border ${statusBadge(n.status)}`}>
                  {n.status}
                </span>
              </div>
              <Bar label="CPU" pct={n.cpu_pct} />
              <Bar label="Memory" pct={n.mem_pct} />
              <Bar label="Disk" pct={n.disk_pct} />
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">
          Cluster balance
        </h2>
        <Balance />
      </section>

      <section>
        <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">
          Recent changes
        </h2>
        <Changes />
      </section>

      {chartNode && (
        <section>
          <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">
            Trend — {chartNode}
          </h2>
          <NodeChart node={chartNode} />
        </section>
      )}

      <section>
        <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">
          Guests ({vms.length})
        </h2>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-[#0d1320] text-gray-400 text-xs">
              <tr>
                <th className="text-left p-2">VMID</th>
                <th className="text-left p-2">Name</th>
                <th className="text-left p-2">Node</th>
                <th className="text-left p-2">Kind</th>
                <th className="text-left p-2">Status</th>
                <th className="text-left p-2 w-32">CPU</th>
                <th className="text-left p-2 w-32">Mem</th>
              </tr>
            </thead>
            <tbody>
              {vms.map((v) => (
                <tr key={v.vmid} className="border-t border-gray-800/60">
                  <td className="p-2 text-gray-400">{v.vmid}</td>
                  <td className="p-2 font-medium">{v.name}</td>
                  <td className="p-2 text-gray-400">{v.node}</td>
                  <td className="p-2 text-gray-500">{v.kind}</td>
                  <td className="p-2">
                    <span className={`text-[10px] px-2 py-0.5 rounded border ${statusBadge(v.status)}`}>
                      {v.status}
                    </span>
                  </td>
                  <td className="p-2"><Bar label="" pct={v.cpu_pct} /></td>
                  <td className="p-2"><Bar label="" pct={v.mem_pct} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <DemoControls vms={vms} />
    </div>
  );
}

function DemoControls({ vms }) {
  const [vmid, setVmid] = useState("");
  const [cpu, setCpu] = useState(100);
  const [msg, setMsg] = useState("");

  async function inject() {
    if (!vmid) return;
    await api.post(`/demo/inject?vmid=${vmid}&cpu_pct=${cpu}`);
    setMsg(`Injecting ${cpu}% CPU on VM ${vmid}`);
  }
  async function clear() {
    await api.post(`/demo/clear`);
    setMsg("Cleared injected load");
  }

  return (
    <section className="rounded-lg border border-gray-800 bg-[#0d1320] p-3">
      <h3 className="text-xs uppercase tracking-wide text-gray-500 mb-2">
        Demo · load injector (mock only)
      </h3>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <select
          value={vmid}
          onChange={(e) => setVmid(e.target.value)}
          className="bg-gray-900 border border-gray-700 rounded px-2 py-1"
        >
          <option value="">choose VM…</option>
          {vms.map((v) => (
            <option key={v.vmid} value={v.vmid}>
              {v.vmid} · {v.name}
            </option>
          ))}
        </select>
        <input
          type="number"
          value={cpu}
          onChange={(e) => setCpu(e.target.value)}
          className="w-20 bg-gray-900 border border-gray-700 rounded px-2 py-1"
        />
        <span className="text-gray-500 text-xs">% CPU</span>
        <button onClick={inject} className="px-3 py-1 rounded bg-amber-700 hover:bg-amber-600">
          Inject
        </button>
        <button onClick={clear} className="px-3 py-1 rounded bg-gray-800 hover:bg-gray-700">
          Clear
        </button>
        {msg && <span className="text-xs text-gray-400">{msg}</span>}
      </div>
    </section>
  );
}
