# Aster-UI Full Integration Plan

**Goal:** Copy the `Aster-UI` dashboard into the Aster-localization root, then wire every
section to the real Python backend — replacing all mock data with live API calls — while
the existing `aster-ui/` floating-head app is left completely untouched.

**Answers that shaped this plan:**
- Old floating-head `aster-ui/` → left as-is, no changes.
- Chat responses → SSE word-by-word streaming (no brain.py surgery required).
- Memory "forget" button → UI-only removal; fact persists on disk.

---

## Architecture Map

```
Aster-UI (Tauri, port 5173 dev)
  │
  ├── GET/POST/PATCH  http://127.0.0.1:8000/api/*   ← face_server.py (FastAPI)
  ├── SSE             http://127.0.0.1:8000/api/chat/stream
  ├── WebSocket       ws://127.0.0.1:8000/api/logs   ← realtime_stream.py
  └── LiveKit SDK     livekit URL from /api/token     ← webrtc_bridge.py

face_server.py imports (read-only):
  tools/sentry.py          (SENTRY_ACTIVE, toggle_sentry_mode)
  tools/gesture.py         (GESTURE_ACTIVE, toggle_gesture_mode)
  tools/awareness.py       (AWARENESS_ACTIVE, toggle_awareness_mode)
  tools/intervention.py    (INTERVENTION_ACTIVE, toggle_intervention_mode)
  tools/emotion_recognition.py  (FACE_EMOTION_ENABLED, toggle_face_emotion)
  tools/karaoke.py         (start_karaoke, stop_karaoke)
  core/memory.py           (store_memory, read_memory_facts)
  config.py                (KARAOKE_DIR, etc.)
```

**Event schema from `realtime_stream.py` WebSocket:**
| type | fields | Aster-UI consumer |
|---|---|---|
| `terminal` | `{type, data: string, ts: ms}` | Activity Log |
| `discord`  | `{type, user, msg, ts}` | Activity Log (secondary) |
| `wake`     | `{type, awake: bool, ts}` | (future) |
| `sentiment`| `{type, sentiment: string, ts}` | (future) |
| `telemetry`| `{type, data: {}, ts}` | (future) |

---

## Pre-Conditions Checklist (verify before starting Phase 0)

- [ ] `main.py` can start successfully (`llama-server` not required; just import chain must not crash)
- [ ] `face_server.py` is already running when `main.py` is launched (it starts on a daemon thread)
- [ ] `http://127.0.0.1:8000/api/token` returns a JSON object (even if LiveKit is unreachable, it should return 200)
- [ ] `ws://127.0.0.1:8000/api/logs` accepts a WebSocket connection
- [ ] Node 18+ and Rust toolchain installed (`node --version`, `rustc --version`)

---

## Full API Contract (all endpoints this plan adds to `face_server.py`)

| Method | Path | Body | Returns | Phase |
|---|---|---|---|---|
| GET | `/api/stats` | — | `{status, memory_count, uptime_seconds, socials}` | 3 |
| GET | `/api/memory` | `?q=` | `MemoryFact[]` | 4 |
| POST | `/api/memory` | `{text}` | `{ok, id}` | 4 |
| GET | `/api/skills` | — | `Skill[]` | 5 |
| PATCH | `/api/skills/{id}` | `{enabled}` | `{id, enabled}` | 5 |
| POST | `/api/chat/stream` | `{text}` | SSE stream | 6 |
| GET | `/api/notes` | — | `Note[]` | 8 |
| POST | `/api/notes` | `{text}` | `{ok, id}` | 8 |
| GET | `/api/karaoke/status` | — | `{playing, song\|null}` | 9 |
| GET | `/api/karaoke/library` | — | `Song[]` | 9 |
| POST | `/api/karaoke/start` | `{song}` | `{ok, cached}` | 9 |
| POST | `/api/karaoke/stop` | — | `{ok}` | 9 |
| GET | `/api/socials` | — | `SocialConnection[]` | 10 |

Existing endpoints (already in `face_server.py`, untouched):
- `GET /api/token` → LiveKit JWT
- `WS /api/logs` → event stream

---

## Phase 0 — Copy Aster-UI into Aster-localization

### What & Why
Move the dashboard project from its standalone location into the Aster-localization root
so it lives alongside the backend it will talk to. Old `aster-ui/` stays untouched.

### Steps

**0.1 — Copy the directory**

Run from PowerShell (not from inside either project):
```powershell
Copy-Item "E:\LLM testing\Aster-UI" "E:\LLM testing\Aster-localization\Aster-UI" -Recurse
```

Expected result: `E:\LLM testing\Aster-localization\Aster-UI\` now exists alongside
`aster-ui\` (the old floating-head).

**0.2 — Install dependencies**

```powershell
cd "E:\LLM testing\Aster-localization\Aster-UI"
npm install
```

**0.3 — Verify `tauri.conf.json` settings**

Open `Aster-UI\src-tauri\tauri.conf.json`. Confirm:
- `"devUrl": "http://localhost:5173"` — Vite dev port (no conflict; old `aster-ui` dev is never run simultaneously)
- `"frontendDist": "../dist"` — production build output
- `"csp": null` — required so `fetch` to `127.0.0.1:8000` works from Tauri's webview

No changes needed.

**0.4 — Start the dev server**

```powershell
npm run dev
```

Open `http://localhost:5173` in a browser.

### Test Checkpoint 0 ✓
- [ ] Browser shows the Aster onboarding screen
- [ ] Complete onboarding (enter a name, pick any model)
- [ ] Navigate through all 9 sections; none throw a JS error in the console
- [ ] Mock data visible in every section (stats, memories, skills, notes, etc.)
- [ ] Animations play smoothly when switching sections

---

## Phase 1 — API Foundation (shared client layer)

### What & Why
Before wiring any section, establish a typed API client so every phase imports from one
place. If the base URL ever changes, one file changes. Also adds the `ASTER_API_BASE`
and `ASTER_WS_BASE` constants.

### Steps

**1.1 — Create `src/api/config.ts`**

```typescript
// Base URLs for the Aster face server running at localhost:8000.
// Both Vite dev (localhost:5173) and Tauri production use these directly;
// CORS is open on the server side (allow_origins=["*"]).
export const ASTER_API_BASE = "http://127.0.0.1:8000";
export const ASTER_WS_BASE  = "ws://127.0.0.1:8000";
```

**1.2 — Create `src/api/client.ts`**

```typescript
import { ASTER_API_BASE } from "./config";

export class AsterAPIError extends Error {
  constructor(public status: number, message: string) {
    super(message);
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
```

**1.3 — Verify face server is reachable from the UI**

Add a temporary one-liner in `src/App.tsx` (remove after this test):
```typescript
import { asterGet } from "./api/client";
asterGet("/api/token").then(console.log).catch(console.error);
```

### Test Checkpoint 1 ✓
- [ ] Start `main.py` so `face_server.py` is running on `:8000`
- [ ] Start `npm run dev` in a separate terminal
- [ ] Open browser DevTools → Console
- [ ] See `{token: "...", ws_url: "...", room: "..."}` logged (or a structured error if LiveKit env vars aren't set — that's also acceptable, what matters is the fetch reached the server)
- [ ] Remove the test import from `App.tsx`

---

## Phase 2 — Activity Log ← WebSocket `/api/logs`

### What & Why
The simplest real data wire. The WS endpoint already exists in `face_server.py`. The
`ActivityLog.tsx` currently fakes a stream with `setInterval`. Replace it with a real
WebSocket that subscribes to `realtime_stream.py` events.

### Backend Changes — `face_server.py`
No changes needed. `WS /api/logs` already exists and pushes JSON events.

### Frontend Changes — `src/sections/ActivityLog.tsx`

Replace the entire file. Key changes:
- Remove `MOCK_ACTIVITY_LOG`, `STREAM_POOL`, and the `setInterval` effect.
- Add a `useEffect` that opens `ws://127.0.0.1:8000/api/logs`.
- Parse incoming messages. Events have shape `{type, data?, ts}`.
- Filter: display `type === "terminal"` events as log lines. Also display `type === "discord"` entries with a `[Discord → user]` prefix.
- Map: derive `level` from the message text —
  - Contains `[ERROR]` or `Error` or `error` → `"error"`
  - Contains `[WARN]` or `Warning` or `warning` → `"warn"`
  - Otherwise → `"info"`
- Reconnect on close with 2-second back-off (the server restarts when `main.py` restarts).
- Keep the existing visual (monospace terminal, dark `#0c0c0c` card, autoscroll).

**Implementation sketch for the new `useEffect`:**
```typescript
useEffect(() => {
  let ws: WebSocket;
  let retryTimer: ReturnType<typeof setTimeout>;

  function connect() {
    ws = new WebSocket(`${ASTER_WS_BASE}/api/logs`);

    ws.onmessage = (ev) => {
      const event = JSON.parse(ev.data as string) as { type: string; data?: string; user?: string; msg?: string; ts: number };

      let message: string;
      if (event.type === "terminal" && event.data) message = event.data;
      else if (event.type === "discord") message = `[Discord → ${event.user}] ${event.msg}`;
      else return; // ignore wake/sentiment/telemetry for now

      const level: ActivityEntry["level"] =
        /error/i.test(message) ? "error" :
        /warn/i.test(message)  ? "warn"  : "info";

      setEntries((prev) => [
        ...prev.slice(-499), // cap at 500 lines
        { id: `ws-${event.ts}-${Math.random()}`, timestamp: new Date(event.ts).toTimeString().slice(0, 8), level, message },
      ]);
    };

    ws.onclose = () => {
      retryTimer = setTimeout(connect, 2000);
    };
  }

  connect();
  return () => { ws.close(); clearTimeout(retryTimer); };
}, []);
```

Import `ASTER_WS_BASE` from `../api/config`.

### Test Checkpoint 2 ✓
- [ ] `main.py` running
- [ ] `npm run dev` running
- [ ] Open Activity Log section — should connect immediately (no simulated lines)
- [ ] In the CLI input, type any message and press Enter — within 1 second a new line appears in the Activity Log showing Aster processing the request
- [ ] Restart `main.py` — Activity Log reconnects automatically within ~2 seconds
- [ ] No mock/fake lines appear; all content is real stdout from main.py

---

## Phase 3 — Dashboard Stats ← `GET /api/stats`

### What & Why
Replace the four hardcoded stat cards (Status/Memories/Socials/Uptime) with real values
fetched from a new lightweight endpoint. No heavy imports — just file reads and timestamps.

### Backend Changes — `face_server.py`

**3.1 — Add imports at the top of `face_server.py`:**
```python
import time
import threading
from pathlib import Path
import config as cfg
```

**3.2 — Add a module-level start time:**
```python
_start_time = time.time()
```
Place this immediately after the imports block.

**3.3 — Add the endpoint:**
```python
@app.get("/api/stats")
async def get_stats() -> dict:
    # Memory count: count non-empty lines in memory.md that look like facts.
    memory_path = Path("Aster_Vault/memory.md")
    memory_count = 0
    if memory_path.exists():
        memory_count = sum(
            1 for line in memory_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )

    # Uptime in seconds since face_server started.
    uptime_seconds = int(time.time() - _start_time)

    # Social connection status — best-effort, never raises.
    spotify_ok = False
    try:
        import tools.media as media_mod
        sp = getattr(media_mod, "sp", None)
        if sp:
            sp.current_user()
            spotify_ok = True
    except Exception:
        pass

    discord_ok = any(
        t.name == "discord-listener" for t in threading.enumerate()
    )
    telegram_ok = any(
        "polling" in (t.name or "").lower() or "telegram" in (t.name or "").lower()
        for t in threading.enumerate()
    )

    return {
        "status": "online",
        "memory_count": memory_count,
        "uptime_seconds": uptime_seconds,
        "socials": {
            "spotify": spotify_ok,
            "discord": discord_ok,
            "telegram": telegram_ok,
        },
    }
```

### Frontend Changes — `src/sections/Dashboard.tsx`

**3.4 — Add a `useStats` hook in `src/api/useStats.ts`:**
```typescript
import { useEffect, useState } from "react";
import { asterGet } from "./client";

export interface AsterStats {
  status: string;
  memory_count: number;
  uptime_seconds: number;
  socials: { spotify: boolean; discord: boolean; telegram: boolean };
}

export function useStats(intervalMs = 30_000) {
  const [stats, setStats] = useState<AsterStats | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetch() {
      try {
        const data = await asterGet<AsterStats>("/api/stats");
        if (!cancelled) setStats(data);
      } catch {
        // backend not running — keep showing previous value or null
      }
    }

    fetch();
    const id = setInterval(fetch, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [intervalMs]);

  return stats;
}
```

**3.5 — Update `Dashboard.tsx`:**
- Import `useStats`.
- Call `const stats = useStats()` inside `Dashboard()`.
- Build `displayStats` from `stats` when available, falling back to the mock `DASHBOARD_STATS` while loading:

```typescript
const displayStats: DashboardStat[] = stats
  ? [
      { id: "status",   label: "Status",   value: stats.status === "online" ? "Online" : "Offline", hint: "Brain running" },
      { id: "memories", label: "Memories", value: String(stats.memory_count), hint: "Facts stored" },
      { id: "socials",  label: "Socials",  value: `${Object.values(stats.socials).filter(Boolean).length} / 3`, hint: "Services connected" },
      { id: "uptime",   label: "Uptime",   value: formatUptime(stats.uptime_seconds) },
    ]
  : DASHBOARD_STATS;
```

Add a helper:
```typescript
function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}
```

### Test Checkpoint 3 ✓
- [ ] `main.py` running
- [ ] Dashboard section shows real values — "Status: Online", a real memory count
- [ ] Uptime increments (refresh the page after 1 minute, value increases)
- [ ] If Spotify isn't authenticated, "Socials: 0 / 3" (or 1 / 3 if Telegram polling is live)
- [ ] If backend is not running, Dashboard shows mock data silently (no crash, no error toast)

---

## Phase 4 — Memory ← `GET /api/memory` + `POST /api/memory`

### What & Why
The Memory section lets users browse, search, and add facts. Reading uses `memory.md`
as the primary source. Writing calls `memorize_fact` which does dual-write (markdown +
ChromaDB) with dedup checking. Forget button stays UI-only (per decision above).

### Backend Changes — `face_server.py`

**4.1 — Add imports:**
```python
import re as _re
from pydantic import BaseModel
```

**4.2 — Add helper to parse `memory.md`:**

The file format needs to be verified before implementing. Open `Aster_Vault/memory.md`
and note the exact line format. Common format is:
```
[2024-01-15 14:23] Mohamed likes jazz music
```
or:
```
**2024-01-15 14:23** — Mohamed likes jazz music
```

Write the parser to match whatever format is actually used. The reference implementation
below assumes the `[timestamp] fact` format — adjust to match reality:

```python
def _parse_memory_facts(limit: int = 200) -> list[dict]:
    """Read memory.md and return parsed facts, newest first."""
    path = Path("Aster_Vault/memory.md")
    if not path.exists():
        return []

    facts = []
    for i, line in enumerate(reversed(path.read_text(encoding="utf-8").splitlines())):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Try to extract timestamp and text from the line.
        m = _re.match(r"\[(.+?)\]\s*(.*)", line)
        if m:
            ts, text = m.group(1), m.group(2).strip()
            source = "discord" if "[discord]" in text.lower() else "conversation"
        else:
            ts, text, source = "", line, "conversation"
        if text:
            facts.append({"id": f"mem-{i}", "text": text, "savedAt": ts, "source": source})
        if len(facts) >= limit:
            break
    return facts
```

**4.3 — Add endpoints:**
```python
class MemoryBody(BaseModel):
    text: str

@app.get("/api/memory")
async def get_memory(q: str = "") -> list[dict]:
    facts = await asyncio.to_thread(_parse_memory_facts)
    if q:
        q_lower = q.lower()
        facts = [f for f in facts if q_lower in f["text"].lower()]
    return facts

@app.post("/api/memory")
async def add_memory(body: MemoryBody) -> dict:
    text = body.text.strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    try:
        # Import the write function from core/memory.py.
        # Check the exact function name in core/memory.py before implementing.
        # It's likely: from core.memory import store_memory  or  write_memory
        from core import memory as mem_mod
        store_fn = getattr(mem_mod, "store_memory", None) or getattr(mem_mod, "write_memory", None)
        if store_fn:
            await asyncio.to_thread(store_fn, text)
        else:
            # Fallback: append directly to memory.md
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open("Aster_Vault/memory.md", "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {text}\n")
        return {"ok": True, "id": f"mem-{int(time.time())}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

> **Pre-implementation check:** Before coding Phase 4, open `core/memory.py` and find
> the function that writes a single fact to memory.md + ChromaDB. Use that exact
> function name in the import above.

### Frontend Changes — `src/sections/Memory.tsx`

**4.4 — Replace mock with real data:**
- Add `useEffect` on mount: `asterGet<MemoryFact[]>("/api/memory")` → `setMemories(data)`
- Add loading state: show a spinner/skeleton while fetching
- Add error state: show an inline error message if fetch fails
- Update `addFact`: call `asterPost("/api/memory", { text })` and on success prepend the fact locally (optimistic update)
- `forget` stays local-only (filter from state, no API call)

**4.5 — Update query string search:**
When `query` changes (debounced ~300ms), call `asterGet<MemoryFact[]>(\`/api/memory?q=${encodeURIComponent(query)}\`)` and replace the displayed list. This delegates filtering to the server.

### Test Checkpoint 4 ✓
- [ ] Memory section loads and shows real facts from `Aster_Vault/memory.md`
- [ ] Searching by a known keyword filters the list correctly
- [ ] Type a new fact, click Save → fact appears at the top of the list immediately (optimistic)
- [ ] Open `Aster_Vault/memory.md` in a text editor → the new fact is on the last line
- [ ] Reload the page → new fact still appears in the list
- [ ] Click the trash icon on a fact → it disappears from the UI but the file is unchanged (verified by checking memory.md)

---

## Phase 5 — Skills ← `GET /api/skills` + `PATCH /api/skills/{id}`

### What & Why
The Skills section shows 5 toggleable daemon capabilities. Each maps to a real toggle
function in its module. Reading the state checks module-level booleans. Writing calls
the toggle functions directly (same functions the brain's `execute_tool()` calls).

### Pre-implementation: Read each tools module

Before writing any code, open these files and record the exact **state variable name**
and **toggle function name**:

| Skill UI id | File | State var (find in file) | Toggle fn (find in file) |
|---|---|---|---|
| `vision` | — | always enabled, non-togglable | — |
| `intervention` | `tools/intervention.py` | e.g. `INTERVENTION_ACTIVE` | e.g. `toggle_intervention_mode()` |
| `awareness` | `tools/awareness.py` | e.g. `AWARENESS_ACTIVE` | e.g. `toggle_awareness_mode()` |
| `gesture` | `tools/gesture.py` | e.g. `GESTURE_ACTIVE` | e.g. `toggle_gesture_mode()` |
| `sentry` | `tools/sentry.py` | e.g. `SENTRY_ACTIVE` | e.g. `toggle_sentry_mode()` |

Fill in the table above before writing Phase 5 backend code.

### Backend Changes — `face_server.py`

**5.1 — Add imports (after filling in the table above):**
```python
import tools.intervention as _intervention
import tools.awareness    as _awareness
import tools.gesture      as _gesture
import tools.sentry       as _sentry
```

**5.2 — Skill registry:**
```python
# Maps frontend skill id → (get_state_fn, toggle_fn, description)
_SKILL_REGISTRY = {
    "vision": {
        "name": "Vision",
        "description": "Screen capture and webcam tools always available",
        "get": lambda: True,          # always on
        "toggle": None,               # not togglable from UI
    },
    "intervention": {
        "name": "Focus Mode",
        "description": "Nudge you back on track when you get distracted",
        "get": lambda: _intervention.INTERVENTION_ACTIVE,  # adjust var name
        "toggle": _intervention.toggle_intervention_mode,  # adjust fn name
    },
    "awareness": {
        "name": "Awareness",
        "description": "Ambient check-ins based on screen & webcam context",
        "get": lambda: _awareness.AWARENESS_ACTIVE,
        "toggle": _awareness.toggle_awareness_mode,
    },
    "gesture": {
        "name": "Gesture Control",
        "description": "Control music & system with hand gestures via webcam",
        "get": lambda: _gesture.GESTURE_ACTIVE,
        "toggle": _gesture.toggle_gesture_mode,
    },
    "sentry": {
        "name": "Sentry Mode",
        "description": "Watch for unrecognized faces while you're away",
        "get": lambda: _sentry.SENTRY_ACTIVE,
        "toggle": _sentry.toggle_sentry_mode,
    },
}
```

**5.3 — Add endpoints:**
```python
@app.get("/api/skills")
async def get_skills() -> list[dict]:
    return [
        {
            "id": skill_id,
            "name": info["name"],
            "description": info["description"],
            "enabled": info["get"](),
        }
        for skill_id, info in _SKILL_REGISTRY.items()
    ]

class SkillPatch(BaseModel):
    enabled: bool

@app.patch("/api/skills/{skill_id}")
async def patch_skill(skill_id: str, body: SkillPatch) -> dict:
    if skill_id not in _SKILL_REGISTRY:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}")

    info = _SKILL_REGISTRY[skill_id]
    current = info["get"]()
    toggle_fn = info["toggle"]

    if toggle_fn is None:
        return {"id": skill_id, "enabled": current, "note": "not togglable"}

    # Only call toggle if the requested state differs from current.
    if body.enabled != current:
        await asyncio.to_thread(toggle_fn)

    return {"id": skill_id, "enabled": info["get"]()}
```

### Frontend Changes — `src/sections/Skills.tsx`

**5.4 — Replace mock with real data:**
- On mount: `asterGet<Skill[]>("/api/skills")` → `setSkills(data)`
- `toggle(id)`: call `asterPatch(\`/api/skills/${id}\`, { enabled: !current })` then update local state with the returned value
- Vision card: render with `enabled = true` and the Toggle set to `disabled` (visually on, not clickable)
- Add loading state (show skeleton cards while fetching)

### Test Checkpoint 5 ✓
- [ ] Skills section loads showing real enabled/disabled state (not mock defaults)
- [ ] Toggle Sentry Mode ON → in a separate terminal, check that `sentry.SENTRY_ACTIVE` is `True`
  (add a `print(sentry.SENTRY_ACTIVE)` to a quick test script or check the Activity Log for a sentry start message)
- [ ] Toggle it OFF → state reverts
- [ ] Refreshing the page shows the same state (it's runtime state, not persisted — verify this expectation matches reality)
- [ ] Vision card's toggle is visible but unclickable

---

## Phase 6 — Chat Text ← SSE `POST /api/chat/stream`

### What & Why
The Voice & Chat panel's text box sends messages to Aster and receives a response that
appears word-by-word. The approach:
1. FastAPI endpoint receives the message text.
2. Runs `process_user_input(text)` in a thread executor (non-blocking for the event loop).
3. Once the full response string is returned, yields it word-by-word via SSE at 25 ms/word.
4. Frontend consumes SSE with the Fetch API (not EventSource, which doesn't support POST).

This requires **zero changes to brain.py** and gives the visual streaming experience.

### Backend Changes — `face_server.py`

**6.1 — Add imports:**
```python
from fastapi.responses import StreamingResponse
```

**6.2 — Add the endpoint:**
```python
class ChatBody(BaseModel):
    text: str

@app.post("/api/chat/stream")
async def chat_stream(body: ChatBody):
    user_text = body.text.strip()
    if not user_text:
        return {"error": "empty"}

    # Import here to avoid circular import at module load time.
    from core.brain import process_user_input

    # Run the blocking brain call in a thread so FastAPI's event loop isn't blocked.
    response_text: str = await asyncio.to_thread(process_user_input, user_text)

    # Normalize: strip any [NATIVE_AUDIO_PAYLOAD:...] tags that might leak through.
    import re
    response_text = re.sub(r"\[NATIVE_[A-Z_]+:.*?\]", "", response_text, flags=re.DOTALL).strip()
    if not response_text:
        response_text = "(No response)"

    async def event_generator():
        words = response_text.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else f" {word}"
            yield f"data: {chunk}\n\n"
            await asyncio.sleep(0.025)  # 25 ms per word ≈ natural reading pace
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

> **Thread safety note:** `process_user_input()` accesses the shared `messages` list and
> `turn_count` in brain.py. A second chat request arriving while the first is still in
> the thread executor will queue on the GIL. For a single-user local app this is
> acceptable. Do not add concurrent locking at this stage.

### Frontend Changes — `src/sections/VoiceChat.tsx`

**6.3 — Add a `useChatStream` hook in `src/api/useChatStream.ts`:**
```typescript
import { ASTER_API_BASE } from "./config";

export type StreamStatus = "idle" | "streaming" | "done" | "error";

export function useChatStream() {
  async function sendMessage(
    text: string,
    onChunk: (chunk: string) => void,
    onDone: () => void,
    onError: (err: string) => void,
  ): Promise<void> {
    try {
      const res = await fetch(`${ASTER_API_BASE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });

      if (!res.ok) { onError(`HTTP ${res.status}`); return; }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6);
          if (data === "[DONE]") { onDone(); return; }
          onChunk(data);
        }
      }
      onDone();
    } catch (err) {
      onError(String(err));
    }
  }

  return { sendMessage };
}
```

**6.4 — Update `VoiceChat.tsx`:**

- Import `useChatStream`.
- Add `const [streaming, setStreaming] = useState(false)` state.
- In `send()`, when draft is non-empty:
  1. Append the user message to `messages` immediately (optimistic).
  2. Append a placeholder Aster message `{ id: "pending", role: "aster", text: "", timestamp: clockTime() }`.
  3. Call `sendMessage(text, onChunk, onDone, onError)`:
     - `onChunk`: find the pending message and append the chunk to its text.
     - `onDone`: replace pending id with a real id; set `streaming = false`.
     - `onError`: replace pending text with `"(error — is Aster running?)"`.
- Disable the Send button while `streaming === true`.
- Remove the `CANNED_REPLY` constant entirely.

**6.5 — Show typing indicator while streaming:**

While `streaming === true`, render a small pulsing dot before the text starts appearing
in the Aster bubble. Once the first chunk arrives, the dot disappears and text fills in.
This can be a simple conditional inside the pending message render:
```typescript
{message.id === "pending" && message.text === "" && (
  <span className="inline-flex gap-1">
    {[0,1,2].map(i => <span key={i} className="h-1.5 w-1.5 rounded-full bg-primary/60 animate-aster-pulse" style={{animationDelay: `${i * 0.2}s`}} />)}
  </span>
)}
```

### Test Checkpoint 6 ✓
- [ ] `main.py` running (llama-server must be running too for brain to work)
- [ ] Type "what time is it?" in the chat input, click Send
- [ ] User message appears immediately
- [ ] Typing indicator dots show for the duration of brain processing
- [ ] Aster's response appears word by word
- [ ] The response is contextually correct (actual current time, not a mock string)
- [ ] Send button is disabled while streaming; re-enables after [DONE]
- [ ] Type a follow-up; Aster's response reflects conversation history
- [ ] If brain is offline (llama-server not running), the Aster bubble shows the error string without crashing

---

## Phase 7 — Voice Call ← LiveKit SDK + `/api/token`

### What & Why
The "Start Call" button in VoiceChat connects to LiveKit and dispatches the Aster voice
agent. `/api/token` already mints the JWT. The frontend needs the LiveKit JS client SDK
to join the room and publish audio.

### Backend Changes
None — `/api/token` is already correct.

### Frontend Changes

**7.1 — Install LiveKit JS client SDK:**
```powershell
cd "E:\LLM testing\Aster-localization\Aster-UI"
npm install livekit-client
```

**7.2 — Create `src/api/useLiveKitCall.ts`:**
```typescript
import { Room, RoomEvent, Track, LocalAudioTrack } from "livekit-client";
import { asterGet } from "./client";

interface TokenResponse {
  token: string;
  ws_url: string;
  room: string;
}

export function useLiveKitCall() {
  let room: Room | null = null;

  async function startCall(
    onStateChange: (onCall: boolean) => void,
    onError: (msg: string) => void,
  ) {
    try {
      const { token, ws_url } = await asterGet<TokenResponse>("/api/token");
      room = new Room();

      room.on(RoomEvent.Disconnected, () => {
        onStateChange(false);
        room = null;
      });

      await room.connect(ws_url, token);
      await room.localParticipant.setMicrophoneEnabled(true);
      onStateChange(true);
    } catch (err) {
      onError(String(err));
    }
  }

  async function endCall(onStateChange: (onCall: boolean) => void) {
    if (room) {
      await room.disconnect();
      room = null;
    }
    onStateChange(false);
  }

  async function setMuted(muted: boolean) {
    if (!room) return;
    await room.localParticipant.setMicrophoneEnabled(!muted);
  }

  return { startCall, endCall, setMuted };
}
```

**7.3 — Update `VoiceChat.tsx` call controls:**
- Import `useLiveKitCall` and call it inside the component.
- Replace the mock `setOnCall` toggle with real `startCall` / `endCall` calls.
- Replace the mock mute toggle with real `setMuted` call.
- On call start error, show a brief error message beneath the orb.
- The presence orb animation already reacts to `onCall` — no animation changes needed.

**7.4 — Allow microphone permission in Tauri:**

Open `Aster-UI/src-tauri/capabilities/default.json` and ensure microphone access is
permitted. Add:
```json
{
  "identifier": "allow-media-devices",
  "windows": ["main"],
  "permissions": ["media-devices:allow-access-media-devices"]
}
```
If the capability key doesn't exist in the schema, use the Tauri v2 `permissions` array
format instead. Check the Tauri version in `Cargo.toml` for the correct syntax.

### Test Checkpoint 7 ✓
- [ ] LiveKit server must be running (the Python `webrtc_bridge.py` connects to it)
- [ ] Click "Start Call" → browser (or Tauri webview) asks for microphone permission → approve
- [ ] Presence orb speeds up and glows (onCall = true)
- [ ] Say "Hey Aster, what's the weather?" aloud
- [ ] Aster responds via TTS through speakers
- [ ] Click "End Call" → orb returns to slow pulse
- [ ] If LiveKit server is not running, an inline error appears under the orb (no uncaught exception)

---

## Phase 8 — Notes ← `GET /api/notes` + `POST /api/notes`

### What & Why
Notes saved via the UI should persist in `Aster_Vault/` and be retrievable across
sessions. Timers/alarms go through the brain's existing tools. The Reminders panel stays
partially mocked (alarms require winotify integration not easily callable from the API).

### Pre-implementation: Read `core/brain.py` around the `save_note` / `get_notes` tool

Find the `save_note` and `get_notes` tool implementations in brain.py's `execute_tool()`
dispatch. Note the actual Python functions they call. The notes are likely stored in a
plain file or SQLite in `Aster_Vault/`. Identify the file path and format.

### Backend Changes — `face_server.py`

**8.1 — Add endpoints (adjust imports/paths after pre-implementation check):**
```python
class NoteBody(BaseModel):
    text: str

@app.get("/api/notes")
async def get_notes_endpoint() -> list[dict]:
    try:
        # Adjust this import to match the actual note storage module/function.
        # Likely in tools/system.py or tools/media.py under save_note/get_notes.
        from tools.system import get_notes as _get_notes
        notes = await asyncio.to_thread(_get_notes)
        # Ensure we return a list[dict] — adapt to actual return format.
        if isinstance(notes, str):
            # If get_notes returns a formatted string, parse it into a list.
            lines = [l.strip() for l in notes.strip().splitlines() if l.strip()]
            return [{"id": f"note-{i}", "text": l, "createdAt": ""} for i, l in enumerate(lines)]
        return notes
    except Exception as e:
        return []

@app.post("/api/notes")
async def add_note_endpoint(body: NoteBody) -> dict:
    text = body.text.strip()
    if not text:
        return {"ok": False, "error": "empty"}
    try:
        from tools.system import save_note as _save_note
        await asyncio.to_thread(_save_note, text)
        return {"ok": True, "id": f"note-{int(time.time())}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

### Frontend Changes — `src/sections/Notes.tsx`

**8.2 — Replace mock with real data:**
- On mount: `asterGet<Note[]>("/api/notes")` → `setNotes(data)`
- `addNote()`: call `asterPost("/api/notes", { text })`, on success prepend to list (optimistic)
- Reminders panel: leave as mock for now (no alarm API wired yet)

### Test Checkpoint 8 ✓
- [ ] Notes section loads real saved notes
- [ ] Add a note via UI → appears immediately in list
- [ ] Restart the app → note still shows (persisted)
- [ ] Reminders panel still shows mock data (expected)

---

## Phase 9 — Karaoke ← `/api/karaoke/*`

### What & Why
The Karaoke UI needs to search for and play instrumental songs. The backend's
`start_karaoke(song)` does download → Demucs separation → ffplay on a daemon thread.
The library endpoint scans `Aster_Vault/Karaoke/` for already-cached songs.

### Backend Changes — `face_server.py`

**9.1 — Add imports:**
```python
from tools.karaoke import start_karaoke as _start_karaoke, stop_karaoke as _stop_karaoke
import config as _cfg

# Runtime state for "is something playing right now"
_karaoke_state: dict = {"playing": False, "song": None}
```

**9.2 — Add endpoints:**
```python
@app.get("/api/karaoke/status")
async def karaoke_status() -> dict:
    return _karaoke_state

@app.get("/api/karaoke/library")
async def karaoke_library() -> list[dict]:
    karaoke_dir = Path(_cfg.KARAOKE_DIR)
    if not karaoke_dir.exists():
        return []
    songs = []
    for song_dir in sorted(karaoke_dir.iterdir()):
        if song_dir.is_dir() and (song_dir / "no_vocals.wav").exists():
            songs.append({
                "id": song_dir.name,
                "title": song_dir.name.replace("-", " ").replace("_", " ").title(),
                "artist": "—",
                "durationSec": 0,   # duration not stored; 0 is fine for the UI
                "cached": True,
            })
    return songs

class KaraokeStartBody(BaseModel):
    song: str

@app.post("/api/karaoke/start")
async def karaoke_start(body: KaraokeStartBody) -> dict:
    song = body.song.strip()
    if not song:
        return {"ok": False, "error": "empty song name"}
    _karaoke_state["playing"] = True
    _karaoke_state["song"] = song
    # start_karaoke is non-blocking (daemon thread); returns immediately.
    await asyncio.to_thread(_start_karaoke, song)
    return {"ok": True, "cached": False}  # cached status not knowable before start

@app.post("/api/karaoke/stop")
async def karaoke_stop() -> dict:
    await asyncio.to_thread(_stop_karaoke)
    _karaoke_state["playing"] = False
    _karaoke_state["song"] = None
    return {"ok": True}
```

### Frontend Changes — `src/sections/Karaoke.tsx`

**9.3 — Redesign the section flow:**

The UI is currently list-based (mock queue). For the real backend, remodel it as:

- **Search bar** (stays): user types a song name.
- **Play button** below the search: clicks `POST /api/karaoke/start` with the search text.
  While processing, show a "Downloading & separating…" status message.
- **Stop button**: `POST /api/karaoke/stop`; clear status.
- **Now Playing card**: poll `GET /api/karaoke/status` every 3 seconds; show `song` name
  when `playing === true`, hide the card when not playing.
- **Library list**: on mount, fetch `GET /api/karaoke/library`; shows cached songs.
  Clicking a cached song name pre-fills the search input.

**9.4 — Update `Karaoke.tsx`:**
- Remove `MOCK_QUEUE`, `NOW_PLAYING` imports.
- Add state: `library`, `statusPlayback`, `statusSong`, `loading`.
- On mount: fetch library. Poll status every 3s while `playing === true`.
- `handlePlay()`: calls `asterPost("/api/karaoke/start", { song: search })`.
  Shows "Preparing your karaoke track…" text while waiting (it can take 30–90 seconds for a cache miss).
- `handleStop()`: calls `asterPost("/api/karaoke/stop")`.
- The progress bar animation stays as a decorative element.

### Test Checkpoint 9 ✓
- [ ] Library list shows any previously cached songs
- [ ] Type a song name (e.g., "Bohemian Rhapsody") → click Play
- [ ] Status message shows "Preparing your karaoke track…"
- [ ] After ~60 seconds, hear the instrumental playing through speakers (yt-dlp + Demucs must be on PATH)
- [ ] "Now Playing" card appears with the song name
- [ ] Click Stop → music stops, "Now Playing" card hides
- [ ] If yt-dlp is not installed, an error appears (check Activity Log for details)

---

## Phase 10 — Socials ← `GET /api/socials`

### What & Why
The Socials section shows which platforms are connected. This is a read-only status
display. The Connect/Disconnect buttons in the current UI don't apply to the real backend
(Spotify OAuth happened once at setup; Telegram/Discord listeners are always-on daemons).
The buttons will be repurposed as "Reconnect" hints for when a service is offline.

### Backend Changes — `face_server.py`

**10.1 — The `/api/stats` endpoint already checks social status.** Refactor: move the
social check into a dedicated `_get_social_status()` helper and call it from both
`/api/stats` and `/api/socials`.

```python
def _get_social_status() -> dict:
    # Spotify
    spotify_ok = False
    spotify_detail = None
    try:
        import tools.media as _media
        sp = getattr(_media, "sp", None)
        if sp:
            user = sp.current_user()
            spotify_ok = True
            spotify_detail = user.get("display_name", "Connected")
    except Exception:
        pass

    # Discord listener thread
    discord_ok = any("discord" in (t.name or "").lower() for t in threading.enumerate())

    # Telegram polling thread
    telegram_ok = any(
        "polling" in (t.name or "").lower() or "telegram" in (t.name or "").lower()
        for t in threading.enumerate()
    )

    return {
        "spotify":  {"connected": spotify_ok,  "detail": spotify_detail},
        "discord":  {"connected": discord_ok,  "detail": None},
        "telegram": {"connected": telegram_ok, "detail": None},
    }

@app.get("/api/socials")
async def get_socials() -> list[dict]:
    status = await asyncio.to_thread(_get_social_status)
    return [
        {
            "id": "spotify",
            "name": "Spotify",
            "description": "Music playback, liked songs, playlists",
            "connected": status["spotify"]["connected"],
            "detail": status["spotify"]["detail"],
        },
        {
            "id": "discord",
            "name": "Discord",
            "description": "Read DMs from friends, reply as Aster",
            "connected": status["discord"]["connected"],
            "detail": None,
        },
        {
            "id": "telegram",
            "name": "Telegram",
            "description": "Remote control, voice notes, photos",
            "connected": status["telegram"]["connected"],
            "detail": None,
        },
    ]
```

**10.2 — Update `/api/stats`** to call `_get_social_status()` instead of inline logic.

### Frontend Changes — `src/sections/Socials.tsx`

**10.3 — Replace mock with real data:**
- On mount: `asterGet<SocialConnection[]>("/api/socials")` → `setSocials(data)`
- The `toggle` function is removed — these are read-only connections.
- The Connect button becomes either:
  - Hidden (if connected)
  - A "Help" link or tooltip explaining how to reconnect (Spotify: re-run `python main.py`; Discord: check token in config.py)
- Auto-refresh every 60 seconds.

**10.4 — Update `types.ts`:**

The Socials `connected` field is already there. Just remove the requirement for
`SocialPlatform` to include toggleability. No type changes needed.

### Test Checkpoint 10 ✓
- [ ] Socials section shows real connection status
- [ ] If `main.py` is running with Telegram active, Telegram shows "Connected"
- [ ] Spotify shows "Connected" and displays the account display name when authenticated
- [ ] Discord shows "Connected" only when the listener thread is alive
- [ ] Connect/Disconnect buttons are replaced with read-only status chips
- [ ] Auto-refresh picks up a newly connected service within 60 seconds

---

## Phase 11 — Production Build & End-to-End Test

### What & Why
Build the Tauri app for daily use (production mode uses ~500 MB less RAM than dev mode,
per the original CLAUDE.md note). Verify all sections against the live backend before
declaring the integration complete.

### Steps

**11.1 — Verify no TypeScript errors:**
```powershell
cd "E:\LLM testing\Aster-localization\Aster-UI"
npx tsc --noEmit
```
Fix any errors before building.

**11.2 — Run the test suite:**
```powershell
npx vitest run
```
All tests in `src/**/*.test.tsx` and `src/**/*.test.ts` must pass.

**11.3 — Production build:**
```powershell
npm run tauri build
```
The built `.exe` installer appears in `src-tauri/target/release/bundle/`.

**11.4 — End-to-end test (with `main.py` + `llama-server` both running):**

| Section | What to verify |
|---|---|
| Dashboard | Real memory count, real uptime, real social status |
| Activity Log | Live lines appear when brain processes something |
| Memory | Add a fact; reload; fact persists |
| Skills | Toggle intervention mode; verify daemon responds |
| Voice & Chat (text) | Multi-turn conversation works; response streams word-by-word |
| Voice & Chat (call) | Start call; speak; hear TTS response |
| Notes | Add a note; verify it's in Aster_Vault on disk |
| Karaoke | Library shows cached songs; search + play works |
| Socials | All connection statuses accurate |
| Settings | Change name; verify name reflects in sidebar immediately |

**11.5 — Update `CLAUDE.md`:**

Add a new entry to the **Key Files** table:
```
| Aster-UI/ | Full dashboard — React/Tauri app; sections wired to face_server.py API; replaces the mock data scaffold |
```

Add to **Running the System**:
```powershell
# Dashboard UI (production — preferred for daily use)
cd Aster-UI && npm run tauri build  # one-time
# Then run the built exe from src-tauri/target/release/bundle/

# Dashboard UI (dev mode — for UI development only)
cd Aster-UI && npm run tauri dev
```

### Test Checkpoint 11 ✓
- [ ] `npx tsc --noEmit` exits 0
- [ ] `npx vitest run` exits 0
- [ ] `npm run tauri build` completes without error
- [ ] Built app launches and all 9 sections show real data
- [ ] Activity Log does NOT show the fake simulated timer lines (mock is fully removed)
- [ ] Closing and reopening the app remembers user name and settings (localStorage persisted)

---

## Appendix A — Complete File Change Summary

### Files modified in `Aster-localization/` (backend):
```
tools/face_server.py          ← primary change: all new API endpoints added here
```

### Files created in `Aster-UI/src/` (frontend):
```
src/api/config.ts             Phase 1 — base URLs
src/api/client.ts             Phase 1 — typed fetch helpers
src/api/useStats.ts           Phase 3 — stats polling hook
src/api/useChatStream.ts      Phase 6 — SSE streaming hook
src/api/useLiveKitCall.ts     Phase 7 — LiveKit room hook
```

### Files modified in `Aster-UI/src/` (frontend):
```
src/sections/ActivityLog.tsx  Phase 2 — real WS, remove mock timer
src/sections/Dashboard.tsx    Phase 3 — useStats, remove mock DASHBOARD_STATS
src/sections/Memory.tsx       Phase 4 — real GET/POST, forget stays local
src/sections/Skills.tsx       Phase 5 — real GET/PATCH, vision non-togglable
src/sections/VoiceChat.tsx    Phase 6+7 — SSE chat, LiveKit call
src/sections/Notes.tsx        Phase 8 — real GET/POST
src/sections/Karaoke.tsx      Phase 9 — redesigned: search→play flow, library
src/sections/Socials.tsx      Phase 10 — read-only status, remove toggle
```

### Files NOT touched:
```
aster-ui/                     (old floating-head — untouched)
core/brain.py                 (no changes required)
main.py                       (no changes required)
All tools/* except face_server.py (imported, not modified)
```

---

## Appendix B — Common Failure Modes & Fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Activity Log never connects | `main.py` not running | Start `main.py`; the face server launches with it |
| Chat returns "(No response)" | `llama-server` not running | Start `start.bat` first |
| Skills toggle has no effect | State var name mismatch | Re-read the tools module and update `_SKILL_REGISTRY` |
| Memory shows 0 facts | Wrong line parser regex | Open `memory.md`, inspect format, adjust `_parse_memory_facts` |
| Karaoke stuck on "Preparing" | yt-dlp or ffplay not on PATH | Add both to system PATH |
| Voice call doesn't start | LiveKit server unreachable | Verify LiveKit URL in config.py and that LiveKit process is running |
| CORS error in browser | FastAPI CORS middleware | Already set to `allow_origins=["*"]`; check no proxy is stripping headers |
| Tauri build fails on Rust | Outdated Rust toolchain | Run `rustup update stable` |

---

## Appendix C — Port Reference

| Service | Port | Notes |
|---|---|---|
| llama-server | 8080 | Must be started before `main.py` |
| face_server (FastAPI) | 8000 | Starts with `main.py` |
| Aster-UI Vite dev | 5173 | `npm run dev` in `Aster-UI/` |
| old aster-ui Vite dev | 5173 | Never run simultaneously with new one |
| LiveKit | configured in config.py | External or local LiveKit server |
