import React, { useState, useRef, useEffect } from "react";
import { api } from "../api";

export default function Chat({ llmEnabled }) {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      text: 'Ask me about the cluster — e.g. "what\'s under pressure right now?"',
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef();

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    if (!input.trim() || busy) return;
    const q = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    try {
      const r = await api.post("/llm/ask", { question: q });
      setMessages((m) => [...m, { role: "assistant", text: r.answer }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `error: ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {!llmEnabled && (
        <div className="m-4 p-3 rounded border border-amber-800 bg-amber-950/30 text-amber-300 text-sm">
          LLM not configured. Set STEWARD_LLM_BASE_URL (e.g. a local Ollama) to chat.
          Monitoring works fully without it.
        </div>
      )}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                m.role === "user"
                  ? "bg-sky-800 text-white"
                  : "bg-[#0d1320] border border-gray-800 text-gray-200"
              }`}
            >
              {m.text}
            </div>
          </div>
        ))}
        {busy && <div className="text-gray-500 text-sm">thinking…</div>}
        <div ref={endRef} />
      </div>
      <div className="p-3 border-t border-gray-800 flex gap-2">
        <input
          value={input}
          disabled={!llmEnabled || busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Ask about the cluster…"
          className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm disabled:opacity-50"
        />
        <button
          onClick={send}
          disabled={!llmEnabled || busy}
          className="px-4 py-2 rounded bg-sky-700 hover:bg-sky-600 text-sm disabled:opacity-40"
        >
          Send
        </button>
      </div>
    </div>
  );
}
