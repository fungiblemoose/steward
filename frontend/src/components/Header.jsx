import React, { useState } from "react";
import { api, setToken, getToken } from "../api";

export default function Header({ flags, wsStatus, onFlags }) {
  const [showToken, setShowToken] = useState(false);
  const [token, setTok] = useState(getToken());

  async function toggle(field) {
    const next = { [field]: !flags[field] };
    const updated = await api.post("/flags", next);
    onFlags(updated);
  }

  const paused = flags?.paused;
  const dryRun = flags?.dry_run;

  return (
    <header className="border-b border-gray-800 bg-[#0d1320] px-5 py-3 flex items-center gap-4 flex-wrap">
      <div className="flex items-center gap-2">
        <span className="text-xl font-semibold tracking-tight">🛡️ Steward</span>
        <span className="text-xs text-gray-500">Proxmox babysitter</span>
      </div>

      <div className="flex items-center gap-2 text-xs">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            wsStatus === "connected" ? "bg-emerald-500" : "bg-red-500"
          }`}
        />
        <span className="text-gray-400">{wsStatus}</span>
      </div>

      <div className="ml-auto flex items-center gap-3 flex-wrap">
        {flags?.proxmox_mode && (
          <span className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-400">
            mode: {flags.proxmox_mode}
          </span>
        )}
        <span
          className={`text-xs px-2 py-1 rounded border ${
            flags?.llm_enabled
              ? "border-sky-700 text-sky-300"
              : "border-gray-700 text-gray-500"
          }`}
        >
          LLM {flags?.llm_enabled ? "on" : "off"}
        </span>

        <button
          onClick={() => toggle("dry_run")}
          className={`text-xs px-3 py-1.5 rounded border font-medium ${
            dryRun
              ? "border-emerald-700 text-emerald-300 bg-emerald-950"
              : "border-red-600 text-red-300 bg-red-950"
          }`}
          title="When ON, no action ever calls the real client"
        >
          dry-run: {dryRun ? "ON" : "OFF"}
        </button>

        <button
          onClick={() => toggle("paused")}
          className={`text-xs px-3 py-1.5 rounded border font-bold ${
            paused
              ? "border-red-500 text-red-200 bg-red-700"
              : "border-gray-600 text-gray-200 bg-gray-800 hover:bg-gray-700"
          }`}
          title="Global kill switch — pauses all action execution"
        >
          {paused ? "⏸ PAUSED (resume)" : "⏻ KILL SWITCH"}
        </button>

        <button
          onClick={() => setShowToken((s) => !s)}
          className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-400"
        >
          🔑
        </button>
      </div>

      {showToken && (
        <div className="w-full flex items-center gap-2 mt-2">
          <input
            type="password"
            value={token}
            onChange={(e) => setTok(e.target.value)}
            placeholder="API token (only if the server requires auth)"
            className="text-sm bg-gray-900 border border-gray-700 rounded px-2 py-1 flex-1"
          />
          <button
            onClick={() => {
              setToken(token);
              location.reload();
            }}
            className="text-sm px-3 py-1 rounded bg-sky-700 hover:bg-sky-600"
          >
            Save & reload
          </button>
        </div>
      )}
    </header>
  );
}
