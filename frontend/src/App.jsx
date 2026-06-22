import React, { useEffect, useState, useCallback } from "react";
import { api, openWebSocket } from "./api";
import Header from "./components/Header";
import Dashboard from "./components/Dashboard";
import Checks from "./components/Checks";
import Actions from "./components/Actions";
import Events from "./components/Events";
import Chat from "./components/Chat";

const TABS = ["Dashboard", "Checks", "Actions", "Events", "Chat"];

export default function App() {
  const [tab, setTab] = useState("Dashboard");
  const [snapshot, setSnapshot] = useState(null);
  const [flags, setFlags] = useState({});
  const [liveEvents, setLiveEvents] = useState([]);
  const [wsStatus, setWsStatus] = useState("connecting");

  const onMessage = useCallback((msg) => {
    if (msg.type === "tick") {
      setSnapshot(msg.snapshot);
      if (msg.flags) setFlags(msg.flags);
      if (msg.events?.length) setLiveEvents(msg.events);
    }
  }, []);

  useEffect(() => {
    api.get("/state").then((s) => {
      if (s.snapshot) setSnapshot(s.snapshot);
      if (s.flags) setFlags(s.flags);
    }).catch(() => {});
    const close = openWebSocket(onMessage, setWsStatus);
    return close;
  }, [onMessage]);

  return (
    <div className="min-h-screen flex flex-col">
      <Header flags={flags} wsStatus={wsStatus} onFlags={setFlags} />

      {flags?.paused && (
        <div className="bg-red-700 text-red-50 text-center text-sm py-1.5 font-medium">
          ⏸ Kill switch engaged — action execution is paused. Suggestions are still logged.
        </div>
      )}

      <nav className="flex gap-1 px-4 pt-3 border-b border-gray-800 bg-[#0b0f17]">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm rounded-t-md border-b-2 ${
              tab === t
                ? "border-sky-500 text-white"
                : "border-transparent text-gray-400 hover:text-gray-200"
            }`}
          >
            {t}
          </button>
        ))}
      </nav>

      <main className="flex-1 overflow-y-auto">
        {tab === "Dashboard" && <Dashboard snapshot={snapshot} />}
        {tab === "Checks" && <Checks llmEnabled={flags?.llm_enabled} />}
        {tab === "Actions" && <Actions />}
        {tab === "Events" && <Events liveEvents={liveEvents} llmEnabled={flags?.llm_enabled} />}
        {tab === "Chat" && <Chat llmEnabled={flags?.llm_enabled} />}
      </main>
    </div>
  );
}
