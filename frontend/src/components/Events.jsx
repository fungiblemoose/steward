import React, { useEffect, useState } from "react";
import { api } from "../api";
import { sevColor, fmtDateTime } from "../util";

export default function Events({ liveEvents, llmEnabled }) {
  const [events, setEvents] = useState([]);
  const [sev, setSev] = useState("");
  const [explain, setExplain] = useState({});

  async function load() {
    const q = sev ? `?severity=${sev}` : "";
    setEvents(await api.get(`/events${q}`));
  }
  useEffect(() => {
    load();
  }, [sev]);
  // refresh when new live events arrive
  useEffect(() => {
    if (liveEvents?.length) load();
  }, [liveEvents]);

  async function doExplain(id) {
    setExplain((e) => ({ ...e, [id]: "…" }));
    try {
      const r = await api.post(`/llm/explain/${id}`);
      setExplain((e) => ({ ...e, [id]: r.answer }));
    } catch (e) {
      setExplain((x) => ({ ...x, [id]: `error: ${e.message}` }));
    }
  }

  return (
    <div className="p-5 space-y-3">
      <div className="flex gap-2 text-sm">
        {["", "info", "warning", "critical"].map((s) => (
          <button
            key={s}
            onClick={() => setSev(s)}
            className={`px-3 py-1 rounded border ${
              sev === s ? "border-sky-600 text-sky-300" : "border-gray-700 text-gray-400"
            }`}
          >
            {s || "all"}
          </button>
        ))}
      </div>

      {events.length === 0 && <div className="text-gray-600 text-sm">No events.</div>}

      <div className="space-y-2">
        {events.map((e) => (
          <div key={e.id} className={`rounded-lg border p-3 ${sevColor(e.severity)}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{e.check_name}</span>
              <span className="text-[11px] opacity-70">{fmtDateTime(e.ts)}</span>
            </div>
            <div className="text-sm mt-1">{e.message}</div>
            <div className="text-[11px] opacity-70 mt-1">target: {e.target}</div>
            {llmEnabled && (
              <button
                onClick={() => doExplain(e.id)}
                className="text-[11px] mt-2 px-2 py-1 rounded border border-current opacity-80 hover:opacity-100"
              >
                Explain
              </button>
            )}
            {explain[e.id] && (
              <div className="text-xs mt-2 p-2 rounded bg-black/30 border border-current/30">
                {explain[e.id]}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
