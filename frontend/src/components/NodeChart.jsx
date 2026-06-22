import React, { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { api } from "../api";

export default function NodeChart({ node }) {
  const [data, setData] = useState([]);

  useEffect(() => {
    let active = true;
    async function load() {
      const since = Date.now() / 1000 - 30 * 60; // last 30 min
      try {
        const rows = await api.get(
          `/metrics/series?kind=node&entity=${encodeURIComponent(node)}&since=${since}`
        );
        if (active)
          setData(
            rows.map((r) => ({
              t: new Date(r.ts * 1000).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              }),
              cpu: r.cpu_pct,
              mem: r.mem_pct,
            }))
          );
      } catch (_) {}
    }
    load();
    const id = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [node]);

  if (!data.length) {
    return (
      <div className="h-48 rounded-lg border border-gray-800 bg-[#0d1320] flex items-center justify-center text-gray-600 text-sm">
        collecting history…
      </div>
    );
  }

  return (
    <div className="h-48 rounded-lg border border-gray-800 bg-[#0d1320] p-2">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 10, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="t" tick={{ fontSize: 10, fill: "#6b7280" }} minTickGap={40} />
          <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "#6b7280" }} />
          <Tooltip
            contentStyle={{ background: "#0b0f17", border: "1px solid #374151", fontSize: 12 }}
          />
          <Line type="monotone" dataKey="cpu" stroke="#38bdf8" dot={false} name="CPU %" />
          <Line type="monotone" dataKey="mem" stroke="#a78bfa" dot={false} name="Mem %" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
