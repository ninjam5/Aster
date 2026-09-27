const BACKEND_URL =
  (import.meta.env.VITE_BACKEND_URL as string | undefined) ?? "http://127.0.0.1:8000";

export const LOGS_WS_URL = (() => {
  try {
    const u = new URL(BACKEND_URL);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    u.pathname = "/api/logs";
    return u.toString();
  } catch {
    return "ws://127.0.0.1:8000/api/logs";
  }
})();

export type TokenResponse = {
  token: string;
  ws_url: string;
  room: string;
};

export async function fetchToken(): Promise<TokenResponse> {
  const res = await fetch(`${BACKEND_URL}/api/token`);
  if (!res.ok) {
    throw new Error(`Token request failed (${res.status})`);
  }
  const data = (await res.json()) as Partial<TokenResponse>;
  if (!data.token || !data.ws_url) {
    throw new Error("Malformed token response from face server.");
  }
  return { token: data.token, ws_url: data.ws_url, room: data.room ?? "" };
}
