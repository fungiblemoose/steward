import React, { useEffect, useState } from "react";
import { api } from "../api";
import { fmtDateTime } from "../util";

const STATUS_STYLE = {
  proposed: "text-amber-300 border-amber-700",
  executed: "text-emerald-300 border-emerald-700",
  rejected: "text-gray-400 border-gray-700",
  blocked: "text-red-300 border-red-700",
  failed: "text-red-300 border-red-700",
  approved: "text-sky-300 border-sky-700",
};

export default function Actions() {
  const [proposed, setProposed] = useState([]);
  const [audit, setAudit] = useState([]);

  async function load() {
    setProposed(await api.get("/actions?status=proposed"));
    setAudit(await api.get("/actions?limit=100"));
  }
  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, []);

  async function approve(id) {
    try {
      await api.post(`/actions/${id}/approve`);
    } catch (e) {
      alert(e.message);
    }
    load();
  }
  async function reject(id) {
    await api.post(`/actions/${id}/reject`);
    load();
  }

  return (
    <div className="p-5 space-y-6">
      <section>
        <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">
          Approval queue ({proposed.length})
        </h2>
        {proposed.length === 0 && (
          <div className="text-gray-600 text-sm">Nothing awaiting approval.</div>
        )}
        <div className="space-y-2">
          {proposed.map((a) => (
            <div key={a.id} className="rounded-lg border border-amber-800/60 bg-amber-950/20 p-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium uppercase text-sm">{a.type}</span>
                <span className="text-xs text-gray-400 font-mono">{JSON.stringify(a.params)}</span>
              </div>
              <div className="text-xs text-gray-400 mt-1">{a.reason}</div>
              <div className="text-[11px] text-gray-500 mt-1">{a.reversibility}</div>
              <div className="flex gap-2 mt-2">
                <button
                  onClick={() => approve(a.id)}
                  className="text-xs px-3 py-1 rounded bg-emerald-700 hover:bg-emerald-600"
                >
                  Approve {`(dry-run if enabled)`}
                </button>
                <button
                  onClick={() => reject(a.id)}
                  className="text-xs px-3 py-1 rounded bg-gray-800 hover:bg-gray-700"
                >
                  Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm uppercase tracking-wide text-gray-500 mb-3">Audit log</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-xs">
            <thead className="bg-[#0d1320] text-gray-400">
              <tr>
                <th className="text-left p-2">When</th>
                <th className="text-left p-2">Type</th>
                <th className="text-left p-2">Params</th>
                <th className="text-left p-2">Status</th>
                <th className="text-left p-2">Dry</th>
                <th className="text-left p-2">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((a) => (
                <tr key={a.id} className="border-t border-gray-800/60 align-top">
                  <td className="p-2 text-gray-500 whitespace-nowrap">
                    {fmtDateTime(a.resolved_at || a.ts)}
                  </td>
                  <td className="p-2 uppercase">{a.type}</td>
                  <td className="p-2 font-mono text-gray-400 max-w-xs truncate">
                    {JSON.stringify(a.params)}
                  </td>
                  <td className="p-2">
                    <span
                      className={`px-2 py-0.5 rounded border ${
                        STATUS_STYLE[a.status] || "border-gray-700"
                      }`}
                    >
                      {a.status}
                    </span>
                  </td>
                  <td className="p-2">{a.dry_run ? "✓" : "—"}</td>
                  <td className="p-2 text-gray-400">{a.outcome}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
