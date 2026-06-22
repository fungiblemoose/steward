export function pctColor(pct) {
  if (pct == null) return "bg-gray-600";
  if (pct >= 90) return "bg-red-500";
  if (pct >= 75) return "bg-amber-500";
  if (pct >= 50) return "bg-yellow-500";
  return "bg-emerald-500";
}

export function sevColor(sev) {
  return (
    {
      info: "text-sky-400 border-sky-700 bg-sky-950",
      warning: "text-amber-300 border-amber-700 bg-amber-950",
      critical: "text-red-300 border-red-700 bg-red-950",
    }[sev] || "text-gray-300 border-gray-700 bg-gray-900"
  );
}

export function statusBadge(status) {
  return (
    {
      running: "text-emerald-300 bg-emerald-950 border-emerald-800",
      online: "text-emerald-300 bg-emerald-950 border-emerald-800",
      stopped: "text-red-300 bg-red-950 border-red-800",
      offline: "text-red-300 bg-red-950 border-red-800",
      paused: "text-amber-300 bg-amber-950 border-amber-800",
    }[status] || "text-gray-300 bg-gray-900 border-gray-700"
  );
}

export function fmtTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString();
}

export function fmtDateTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}
