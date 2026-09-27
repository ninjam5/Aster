import { ASTER_API_BASE } from "./config";

export class AsterAPIError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function asterGet<T>(path: string): Promise<T> {
  const res = await fetch(`${ASTER_API_BASE}${path}`);
  if (!res.ok) throw new AsterAPIError(res.status, await res.text());
  return res.json() as Promise<T>;
}

export async function asterPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${ASTER_API_BASE}${path}`, {
    method: "POST",
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new AsterAPIError(res.status, await res.text());
  return res.json() as Promise<T>;
}

export async function asterPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${ASTER_API_BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new AsterAPIError(res.status, await res.text());
  return res.json() as Promise<T>;
}
