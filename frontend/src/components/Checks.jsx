import React, { useEffect, useState } from "react";
import { api } from "../api";
import { sevColor } from "../util";

export default function Checks({ llmEnabled }) {
  const [checks, setChecks] = useState([]);
  const [nl, setNl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setChecks(await api.get("/checks"));
  }
  useEffect(() => {
    load();
  }, []);

  async function toggle(id) {
    await api.post(`/checks/${id}/toggle`);
    load();
  }
  async function remove(id) {
    if (!confirm(`Delete check ${id}?`)) return;
    await api.del(`/checks/${id}`);
    load();
  }
  async function createFromNL() {
    setError("");
    setBusy(true);
    try {
      const chk = await api.post("/llm/check", { request: nl });
      setNl("");
      await load();
      alert(`Created (disabled, awaiting review): ${chk.name}`);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="p-5 space-y-5">
      <section className="rounded-lg border border-gray-800 bg-[#0d1320] p-4">
        <h3 className="text-sm font-medium mb-2">Create a check in plain English</h3>
        {!llmEnabled && (
          <p className="text-xs text-amber-400 mb-2">
            LLM not configured — set STEWARD_LLM_BASE_URL to enable. You can still
            create checks via the API.
          </p>
        )}
        <div className="flex gap-2">
          <input
            value={nl}
            disabled={!llmEnabled || busy}
            onChange={(e) => setNl(e.target.value)}
            placeholder='e.g. "alert me if any node memory goes over 85%"'
            className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm disabled:opacity-50"
            onKeyDown={(e) => e.key === "Enter" && nl && createFromNL()}
          />
          <button
            onClick={createFromNL}
            disabled={!llmEnabled || busy || !nl}
            className="px-4 py-2 rounded bg-sky-700 hover:bg-sky-600 text-sm disabled:opacity-40"
          >
            {busy ? "Generating…" : "Generate"}
          </button>
        </div>
        {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
        <p className="text-xs text-gray-500 mt-2">
          LLM-generated checks are created <b>disabled</b> and must be reviewed and
          enabled by you before they fire.
        </p>
      </section>

      <section className="space-y-2">
        {checks.map((c) => (
          <div
            key={c.id}
            className="rounded-lg border border-gray-800 bg-[#0d1320] p-3 flex items-start gap-3"
          >
            <button
              onClick={() => toggle(c.id)}
              className={`mt-0.5 text-xs px-2 py-1 rounded border ${
                c.enabled
                  ? "border-emerald-700 text-emerald-300 bg-emerald-950"
                  : "border-gray-700 text-gray-500"
              }`}
            >
              {c.enabled ? "enabled" : "disabled"}
            </button>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium">{c.name}</span>
                <span className={`text-[10px] px-2 py-0.5 rounded border ${sevColor(c.severity)}`}>
                  {c.severity}
                </span>
                <span className="text-[10px] px-2 py-0.5 rounded border border-gray-700 text-gray-400">
                  {c.source}
                </span>
                {c.auto_execute && (
                  <span className="text-[10px] px-2 py-0.5 rounded border border-red-700 text-red-300">
                    auto-execute
                  </span>
                )}
              </div>
              <div className="text-xs text-gray-400 mt-1 font-mono">
                {c.probe_type} · {c.target} · {c.condition.metric} {c.condition.op}{" "}
                {c.condition.threshold_str ?? c.condition.threshold}
              </div>
              {c.description && (
                <div className="text-xs text-gray-500 mt-1">{c.description}</div>
              )}
            </div>
            <button
              onClick={() => remove(c.id)}
              className="text-xs text-gray-500 hover:text-red-400"
            >
              delete
            </button>
          </div>
        ))}
      </section>
    </div>
  );
}
