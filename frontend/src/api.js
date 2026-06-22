// Thin API client. Auth token (if the backend requires one) is kept in
// localStorage and sent as a Bearer header / ws query param.

const TOKEN_KEY = "steward_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

function headers(extra = {}) {
  const h = { "Content-Type": "application/json", ...extra };
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (_) {}
    throw new Error(`${res.status}: ${detail}`);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

export const api = {
  get: (p) => fetch(`/api${p}`, { headers: headers() }).then(handle),
  post: (p, body) =>
    fetch(`/api${p}`, {
      method: "POST",
      headers: headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(handle),
  put: (p, body) =>
    fetch(`/api${p}`, { method: "PUT", headers: headers(), body: JSON.stringify(body) }).then(
      handle
    ),
  del: (p) => fetch(`/api${p}`, { method: "DELETE", headers: headers() }).then(handle),
};

// Tier-0 autonomous balancer dry-run preview. Returns the balancer's current
// view (blended imbalance vs threshold) and the migrations it *would* make
// right now — nothing is executed.
export function fetchBalancerSimulation() {
  return api.get("/balancer/simulate");
}

export function openWebSocket(onMessage, onStatus) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const t = getToken();
  const url = `${proto}://${location.host}/ws${t ? `?token=${encodeURIComponent(t)}` : ""}`;
  let ws;
  let closed = false;
  let retry;

  function connect() {
    ws = new WebSocket(url);
    ws.onopen = () => onStatus && onStatus("connected");
    ws.onclose = () => {
      onStatus && onStatus("disconnected");
      if (!closed) retry = setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch (_) {}
    };
  }
  connect();
  return () => {
    closed = true;
    clearTimeout(retry);
    if (ws) ws.close();
  };
}
