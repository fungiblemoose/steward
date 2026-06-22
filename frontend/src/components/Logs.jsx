import React, { useEffect, useState } from "react";
import { api } from "../api";
import { fmtDateTime } from "../util";

const LEVEL_COLOR = {
  DEBUG: "text-gray-500",
  INFO: "text-sky-300",
  WARNING: "text-amber-300",
  ERROR: "text-red-300",
  CRITICAL: "text-red-400",
};

export default function Logs() {
  const [logs, setLogs] = useState([]);
  const [level, setLevel] = useState("");

  async function load() {
    const q = level ? `?level=${level}` : "";
    try {
      setLogs(await api.get(`/logs${q}`));
    } catch (_) {}
  }
  useEffect(() => {
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, [level]);

  return (
    <div className="p-5 space-y-3">
      <div className="flex gap-2 text-sm">
        {["", "INFO", "WARNING", "ERROR"].map((l) => (
          <button
            key={l}
            onClick={() => setLevel(l)}
            className={`px-3 py-1 rounded border ${
              level === l ? "border-sky-600 text-sky-300" : "border-gray-700 text-gray-400"
            }`}
          >
            {l || "all"}
          </button>
        ))}
      </div>
      <div className="rounded-lg border border-gray-800 bg-black/40 p-3 font-mono text-xs space-y-0.5 max-h-[70vh] overflow-y-auto">
        {logs.length === 0 && <div className="text-gray-600">no log records</div>}
        {logs.map((r, i) => (
          <div key={i} className="flex gap-2">
            <span className="text-gray-600 whitespace-nowrap">{fmtDateTime(r.ts)}</span>
            <span className={`${LEVEL_COLOR[r.level] || "text-gray-300"} w-16`}>{r.level}</span>
            <span className="text-gray-500 w-40 truncate">{r.logger}</span>
            <span className="text-gray-200 flex-1 break-all">{r.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
