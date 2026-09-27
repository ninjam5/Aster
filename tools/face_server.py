"""
Aster Face Server — local HTTP/WS backend for the Tauri desktop companion UI.

Serves two things on port 8000:
  GET /api/token  → a LiveKit JWT that joins room "aster-command-center" AND
                    explicitly dispatches the "aster" voice agent into that room.
  WS  /api/logs   → live stdout stream (fed by tools/diagnostics.py).

The agent (webrtc_bridge.py) is registered with agent_name="aster", which disables
automatic dispatch — so the client token MUST carry a RoomAgentDispatch or the call
connects silently with no Aster present.
"""

import asyncio
import contextlib
import os
import re as _re
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from livekit.api import AccessToken, RoomAgentDispatch, RoomConfiguration, VideoGrants

import tools.realtime_stream as realtime_stream
import tools.intervention as _intervention
import tools.awareness    as _awareness
import tools.gesture      as _gesture
import tools.sentry       as _sentry

# Importing webrtc_bridge sets LIVEKIT_URL/API_KEY/API_SECRET via os.environ.setdefault.
# main.py already imports it, but import here too so face_server works standalone.
import webrtc_bridge  # noqa: F401

_start_time = time.time()

ROOM_NAME = os.getenv("LIVEKIT_ROOM", "aster-command-center")
AGENT_NAME = "aster"
_PORT = 8000

app = FastAPI(title="Aster Face Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # local single-user app; Tauri origin is tauri://localhost
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(realtime_stream.stream_dispatch_loop())


@app.get("/api/token")
async def get_token() -> dict[str, str]:
    """Mint a LiveKit token that joins the room and dispatches the Aster agent."""
    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    ws_url = os.environ.get("LIVEKIT_URL", "")
    if not (api_key and api_secret and ws_url):
        raise HTTPException(
            status_code=503,
            detail="LiveKit is not configured — set livekit.url/api_key/api_secret in secrets.yaml.",
        )

    token = (
        AccessToken(api_key, api_secret)
        .with_identity("aster-web-client")
        .with_name(_cfg.OWNER_NAME)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=ROOM_NAME,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .with_room_config(
            RoomConfiguration(agents=[RoomAgentDispatch(agent_name=AGENT_NAME)])
        )
        .to_jwt()
    )
    return {"token": token, "ws_url": ws_url, "room": ROOM_NAME}


@app.websocket("/api/logs")
async def logs_stream(websocket: WebSocket) -> None:
    await realtime_stream.register_stream_client(websocket)
    try:
        while True:
            # The dispatch loop pushes events; we only need to detect disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await realtime_stream.unregister_stream_client(websocket)


# ── Stats (Phase 3) ──────────────────────────────────────────────────────────

def _get_social_status() -> dict:
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

    discord_ok = any("discord" in (t.name or "").lower() for t in threading.enumerate())
    telegram_ok = any(
        "polling" in (t.name or "").lower() or "telegram" in (t.name or "").lower()
        for t in threading.enumerate()
    )

    return {
        "spotify":  {"connected": spotify_ok,  "detail": spotify_detail},
        "discord":  {"connected": discord_ok,  "detail": None},
        "telegram": {"connected": telegram_ok, "detail": None},
    }


@app.get("/api/stats")
async def get_stats() -> dict:
    memory_path = Path("Aster_Vault/memory.md")
    memory_count = 0
    if memory_path.exists():
        memory_count = sum(
            1 for line in memory_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )

    uptime_seconds = int(time.time() - _start_time)
    status = await asyncio.to_thread(_get_social_status)

    return {
        "status": "online",
        "memory_count": memory_count,
        "uptime_seconds": uptime_seconds,
        "socials": {
            "spotify": status["spotify"]["connected"],
            "discord": status["discord"]["connected"],
            "telegram": status["telegram"]["connected"],
        },
    }


# ── Memory (Phase 4) ──────────────────────────────────────────────────────────

def _parse_memory_facts(limit: int = 200) -> list[dict]:
    path = Path("Aster_Vault/memory.md")
    if not path.exists():
        return []

    facts = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(reversed(lines)):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _re.match(r"- \*\*\[(.+?)\]\*\*\s*(.*)", line)
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


class MemoryBody(BaseModel):
    text: str


@app.get("/api/memory")
async def get_memory(q: str = "") -> list[dict]:
    facts = await asyncio.to_thread(_parse_memory_facts)
    if q:
        q_lower = q.lower()
        facts = [f for f in facts if q_lower in f["text"].lower()]
    return facts


@app.post("/api/memory/delete")
async def delete_memory_endpoint(body: MemoryBody) -> dict:
    text = body.text.strip()
    if not text:
        return {"ok": False, "error": "empty"}
    try:
        from core.memory import forget_fact
        result = await asyncio.to_thread(forget_fact, text)
        return {"ok": True, "detail": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/memory")
async def add_memory(body: MemoryBody) -> dict:
    text = body.text.strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    try:
        from core.memory import memorize_fact
        result = await asyncio.to_thread(memorize_fact, text)
        return {"ok": True, "id": f"mem-{int(time.time())}", "detail": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Skills (Phase 5) ──────────────────────────────────────────────────────────

_SKILL_REGISTRY: dict = {
    "vision": {
        "name": "Vision",
        "description": "Screen capture and webcam tools always available",
        "get": lambda: True,
        "set": None,
    },
    "intervention": {
        "name": "Focus Mode",
        "description": "Nudge you back on track when you get distracted",
        "get": lambda: _intervention.INTERVENTION_ACTIVE,
        "set": lambda state: _intervention.toggle_intervention(state),
    },
    "awareness": {
        "name": "Awareness",
        "description": "Ambient check-ins based on screen & webcam context",
        "get": lambda: _awareness.AWARENESS_ACTIVE,
        "set": lambda state: setattr(_awareness, "AWARENESS_ACTIVE", state),
    },
    "gesture": {
        "name": "Gesture Control",
        "description": "Control music & system with hand gestures via webcam",
        "get": lambda: _gesture.GESTURE_ACTIVE,
        "set": lambda state: setattr(_gesture, "GESTURE_ACTIVE", state),
    },
    "sentry": {
        "name": "Sentry Mode",
        "description": "Watch for unrecognized faces while you're away",
        "get": lambda: _sentry.SENTRY_ACTIVE,
        "set": lambda state: setattr(_sentry, "SENTRY_ACTIVE", state),
    },
}


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
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}")

    info = _SKILL_REGISTRY[skill_id]
    current = info["get"]()
    set_fn = info["set"]

    if set_fn is None:
        return {"id": skill_id, "enabled": current, "note": "not togglable"}

    if body.enabled != current:
        await asyncio.to_thread(set_fn, body.enabled)

    return {"id": skill_id, "enabled": info["get"]()}


# ── Chat streaming (Phase 6) ─────────────────────────────────────────────────

class ChatBody(BaseModel):
    text: str


@app.post("/api/chat/stream")
async def chat_stream(body: ChatBody):
    user_text = body.text.strip()
    if not user_text:
        return {"error": "empty"}

    from core.brain import process_user_input

    response_text: str = await asyncio.to_thread(process_user_input, user_text)

    response_text = _re.sub(r"\[NATIVE_[A-Z_]+:.*?\]", "", response_text, flags=_re.DOTALL).strip()
    if not response_text:
        response_text = "(No response)"

    async def event_generator():
        words = response_text.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else f" {word}"
            yield f"data: {chunk}\n\n"
            await asyncio.sleep(0.025)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Notes (Phase 8) ───────────────────────────────────────────────────────────

class NoteBody(BaseModel):
    text: str


@app.get("/api/notes")
async def get_notes_endpoint() -> list[dict]:
    try:
        from tools.notes import get_notes as _get_notes
        raw = await asyncio.to_thread(_get_notes)
        if isinstance(raw, str):
            if raw.startswith("[No notes") or not raw.strip():
                return []
            lines = [l.strip() for l in raw.strip().splitlines() if l.strip() and not l.startswith("#")]
            result = []
            for i, line in enumerate(reversed(lines)):
                m = _re.match(r"- \*\*(.+?)\*\*\s*[—\-]\s*(.*)", line)
                if m:
                    ts, text = m.group(1).strip(), m.group(2).strip()
                else:
                    ts, text = "", line.lstrip("- ").strip()
                if text:
                    result.append({"id": f"note-{i}", "text": text, "createdAt": ts})
            return result
        return raw
    except Exception:
        return []


@app.post("/api/notes")
async def add_note_endpoint(body: NoteBody) -> dict:
    text = body.text.strip()
    if not text:
        return {"ok": False, "error": "empty"}
    try:
        from tools.notes import save_note as _save_note
        await asyncio.to_thread(_save_note, text)
        return {"ok": True, "id": f"note-{int(time.time())}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/notes/delete")
async def delete_note_endpoint(body: NoteBody) -> dict:
    text = body.text.strip()
    if not text:
        return {"ok": False, "error": "empty"}
    try:
        from tools.notes import delete_note as _delete_note
        result = await asyncio.to_thread(_delete_note, text)
        return {"ok": True, "detail": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Socials (Phase 10) ────────────────────────────────────────────────────────

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


# ── Persona (Phase 11) ───────────────────────────────────────────────────────

import config as _cfg  # needed for live persona/voice/llm reads


class PersonaSelectBody(BaseModel):
    id: str


class PersonaCreateBody(BaseModel):
    name: str
    body: str   # raw system-prompt text (personality only)


class PersonaGenerateBody(BaseModel):
    name: str
    answers: dict  # {role_description, communication_style, quirks, formality}


@app.get("/api/persona")
async def get_persona() -> dict:
    from core.brain import list_personas
    personas = await asyncio.to_thread(list_personas)
    return {"active": _cfg.SYSTEM_PROMPT, "personas": personas}


@app.post("/api/persona/select")
async def select_persona(body: PersonaSelectBody) -> dict:
    pid = body.id.strip()
    if not pid:
        raise HTTPException(status_code=400, detail="empty id")
    try:
        from core.brain import reload_persona
        from tools.config_writer import set_config_values
        await asyncio.to_thread(reload_persona, pid)
        await asyncio.to_thread(set_config_values, {"persona.system_prompt": pid})
        return {"ok": True, "active": pid}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/persona/create")
async def create_persona(body: PersonaCreateBody) -> dict:
    """Save a user-written personality-only system prompt as a new persona."""
    import re as _re2
    name = body.name.strip()
    text = body.body.strip()
    if not name or not text:
        raise HTTPException(status_code=400, detail="name and body required")
    slug = _re2.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "custom"
    path = Path(_cfg.SYSTEM_PROMPTS_DIR) / f"{slug}.md"
    try:
        path.write_text(text, encoding="utf-8")
        return {"ok": True, "id": slug}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_PERSONA_GEN_META = """You are a persona-writing assistant for Aster, an AI agent.
Your task is to write a PERSONALITY-ONLY system prompt for a new Aster persona.

CRITICAL RULE: Do NOT include any tool-use rules, memory instructions, shutdown
rules, Discord relay rules, or UI automation rules. Those are handled globally.
Write ONLY personality: who this persona is, how they talk, their tone, quirks,
what they call the user, and any specific behavioral style. Keep it under 500 words.

The new persona should be named: {name}

Here is what the user told you about this persona:
- Role / character description: {role_description}
- Communication style: {communication_style}
- Quirks or catchphrases: {quirks}
- Formality level: {formality}
- Gender of this persona: {gender}

Write the personality system prompt now. Output only the prompt text itself,
no preamble, no explanation, no markdown header."""


def _generate_persona_sync(name: str, answers: dict) -> tuple[str, str]:
    """Synchronous: call the LLM to generate a persona body, save it, return (slug, body)."""
    import re as _re2
    from core.brain import _execute_llm_completion, _build_system_content
    import config as _c

    meta = _PERSONA_GEN_META.format(
        name=name,
        role_description=answers.get("role_description", ""),
        communication_style=answers.get("communication_style", ""),
        quirks=answers.get("quirks", "none"),
        formality=answers.get("formality", "neutral"),
        gender=answers.get("gender", "neutral"),
    )
    gen_messages = [
        {"role": "user", "content": meta},
    ]
    body = _execute_llm_completion(gen_messages, temperature=0.8, n_predict=600).get("content") or ""
    body = body.strip()

    slug = _re2.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "custom"
    path = Path(_c.SYSTEM_PROMPTS_DIR) / f"{slug}.md"
    path.write_text(body, encoding="utf-8")
    return slug, body


@app.post("/api/persona/generate")
async def generate_persona(body: PersonaGenerateBody) -> dict:
    """Let Aster generate a personality-only persona from guided-form answers."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        slug, persona_body = await asyncio.to_thread(
            _generate_persona_sync, name, body.answers
        )
        return {"ok": True, "id": slug, "body": persona_body}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/persona/{persona_id}")
async def delete_persona(persona_id: str) -> dict:
    _BUILTIN = {"jarvis", "gogi"}
    if persona_id in _BUILTIN:
        raise HTTPException(status_code=403, detail="Cannot delete built-in personas.")
    path = Path(_cfg.SYSTEM_PROMPTS_DIR) / f"{persona_id}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found.")
    try:
        path.unlink()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Presets (Phase 12b) ──────────────────────────────────────────────────────

class PresetCreateBody(BaseModel):
    name: str
    persona: str
    voice: str


class PresetSelectBody(BaseModel):
    id: str


@app.get("/api/presets")
async def get_presets() -> dict:
    from tools.presets import load_presets
    presets = await asyncio.to_thread(load_presets)
    return {"presets": presets}


@app.post("/api/presets")
async def create_preset(body: PresetCreateBody) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        from tools.presets import save_preset
        preset_id = await asyncio.to_thread(save_preset, name, body.persona.strip(), body.voice.strip())
        return {"ok": True, "id": preset_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/preset/select")
async def select_preset(body: PresetSelectBody) -> dict:
    """Apply a preset — switches both persona and voice atomically and persists both."""
    pid = body.id.strip()
    if not pid:
        raise HTTPException(status_code=400, detail="id required")
    try:
        from tools.presets import get_preset
        from core.brain import reload_persona
        from tools.config_writer import set_config_values
        preset = await asyncio.to_thread(get_preset, pid)
        persona = preset["persona"]
        voice = preset["voice"]
        await asyncio.to_thread(reload_persona, persona)
        await asyncio.to_thread(set_config_values, {
            "persona.system_prompt": persona,
            "settings.voice_name": voice,
        })
        return {"ok": True, "persona": persona, "voice": voice}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str) -> dict:
    try:
        from tools.presets import delete_preset as _del
        await asyncio.to_thread(_del, preset_id)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Voices (Phase 12) ────────────────────────────────────────────────────────

class VoiceSelectBody(BaseModel):
    voice: str


class VoicePreviewBody(BaseModel):
    voice: str


@app.get("/api/voices")
async def get_voices() -> dict:
    from tools.audio import KOKORO_VOICES, list_custom_voices
    custom = await asyncio.to_thread(list_custom_voices)
    return {"active": _cfg.VOICE_NAME, "voices": KOKORO_VOICES + custom}


@app.post("/api/voice/select")
async def select_voice(body: VoiceSelectBody) -> dict:
    voice = body.voice.strip()
    if not voice:
        raise HTTPException(status_code=400, detail="empty voice")
    try:
        from tools.config_writer import set_config_values
        await asyncio.to_thread(set_config_values, {"settings.voice_name": voice})
        return {"ok": True, "active": voice}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/voice/preview")
async def preview_voice(body: VoicePreviewBody) -> StreamingResponse:
    voice = body.voice.strip()
    if not voice:
        raise HTTPException(status_code=400, detail="empty voice")
    try:
        from tools.audio import synthesize_preview
        wav_path = await asyncio.to_thread(synthesize_preview, voice)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    async def _wav_iter():
        with open(wav_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk
        # Clean up temp file after streaming
        try:
            os.unlink(wav_path)
        except Exception:
            pass

    return StreamingResponse(_wav_iter(), media_type="audio/wav")


# ── LLM Settings (Phase 13) ──────────────────────────────────────────────────

class LlmSettingsBody(BaseModel):
    temperature: float = None
    top_p: float = None
    top_k: int = None
    context_window: int = None
    kv_cache_type: str = None


@app.get("/api/llm-settings")
async def get_llm_settings() -> dict:
    import re as _re2
    kv = "q4_0"  # default
    try:
        bat = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "start.bat"
        if bat.exists():
            txt = bat.read_text(encoding="utf-8")
            m = _re2.search(r"-ctk\s+(\S+)", txt)
            if m:
                kv = m.group(1)
    except Exception:
        pass
    return {
        "temperature":    _cfg.LLM_TEMPERATURE,
        "top_p":          _cfg.LLM_TOP_P,
        "top_k":          _cfg.LLM_TOP_K,
        "context_window": _cfg.N_CTX,
        "kv_cache_type":  kv,
    }


@app.post("/api/llm-settings")
async def set_llm_settings(body: LlmSettingsBody) -> dict:
    from tools.config_writer import set_config_values, rewrite_startbat

    live_updates: dict = {}
    restart_required = False

    if body.temperature is not None:
        live_updates["settings.llm_temperature"] = float(body.temperature)
    if body.top_p is not None:
        live_updates["settings.llm_top_p"] = float(body.top_p)
    if body.top_k is not None:
        live_updates["settings.llm_top_k"] = int(body.top_k)

    if live_updates:
        await asyncio.to_thread(set_config_values, live_updates)

    if body.context_window is not None or body.kv_cache_type is not None:
        restart_required = True
        restart_updates: dict = {}
        if body.context_window is not None:
            restart_updates["runtime.context_window"] = int(body.context_window)
        await asyncio.to_thread(set_config_values, restart_updates)
        await asyncio.to_thread(
            rewrite_startbat,
            body.context_window,
            body.kv_cache_type,
        )

    return {"ok": True, "restart_required": restart_required}


class GoogleTierBody(BaseModel):
    tier: str  # "limited" | "partial" | "autonomous"


def _google_scope_sufficient(google_auth) -> bool:
    """True if the cached token (if any) already covers the currently
    configured tier's scopes — False means the dashboard should show
    'reconnect' rather than treating the account as fully ready."""
    creds = google_auth._credentials or google_auth._load_cached_credentials()
    if not creds:
        return False
    needed = set(google_auth._scopes_for_tier())
    return not (needed - set(creds.scopes or []))


@app.get("/api/google/status")
async def google_status() -> dict:
    import tools.google_auth as google_auth
    connected = await asyncio.to_thread(google_auth.is_connected)
    scope_ok = await asyncio.to_thread(_google_scope_sufficient, google_auth) if connected else False
    unread = 0
    if connected and scope_ok:
        from tools.gmail_tool import get_unread_primary_count
        unread = await asyncio.to_thread(get_unread_primary_count)
    return {
        "configured": _cfg.GOOGLE_AVAILABLE,
        "connected": connected,
        "needs_reconnect": connected and not scope_ok,
        "tier": _cfg.GOOGLE_ACCESS_TIER,
        "reauth_needed": google_auth.reauth_needed(),
        "unread_count": unread,
    }


@app.post("/api/google/connect")
async def google_connect() -> dict:
    """Runs the interactive OAuth consent flow — opens the OS browser and
    blocks this request until the user completes it, same as Spotify's
    first-use flow. May take a while; the frontend should show it as pending."""
    if not _cfg.GOOGLE_AVAILABLE:
        raise HTTPException(status_code=400, detail="Set google.client_id/client_secret in secrets.yaml first.")
    import tools.google_auth as google_auth
    try:
        await asyncio.to_thread(google_auth.connect_google_account)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/google/tier")
async def google_set_tier(body: GoogleTierBody) -> dict:
    tier = body.tier.strip().lower()
    if tier not in ("limited", "partial", "autonomous"):
        raise HTTPException(status_code=400, detail="tier must be limited, partial, or autonomous")
    from tools.config_writer import set_config_values
    await asyncio.to_thread(set_config_values, {"integrations.google.access_tier": tier})
    # A tier upgrade may need broader scopes than the cached token covers —
    # the frontend re-polls /api/google/status right after this and shows
    # "reconnect" via needs_reconnect rather than the user finding out only
    # when a tool call fails later.
    import tools.google_auth as google_auth
    needs_reconnect = await asyncio.to_thread(google_auth.is_connected) and not await asyncio.to_thread(
        _google_scope_sufficient, google_auth
    )
    return {"ok": True, "tier": tier, "needs_reconnect": needs_reconnect}


def start_face_server() -> None:
    """Run the face server (blocking) — launch in a daemon thread from main.py."""
    print(f"[Aster Face] Token + log server starting on http://127.0.0.1:{_PORT}")
    try:
        uvicorn.run(app, host="127.0.0.1", port=_PORT, log_level="warning")
    except Exception as e:
        print(f"[Aster Face] Server stopped: {e}")


if __name__ == "__main__":
    start_face_server()
