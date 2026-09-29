import base64
import asyncio
import contextlib
import os
import shutil
import subprocess
import threading

# from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
# from fastapi.middleware.cors import CORSMiddleware
# from livekit import api as livekit_api
# import uvicorn

# Add NVIDIA CUDA library paths for CTranslate2
_nvidia_base = os.path.join(os.path.dirname(os.__file__), "site-packages", "nvidia")
for _lib in ("cublas", "cuda_nvrtc"):
    _path = os.path.join(_nvidia_base, _lib, "bin")
    if os.path.isdir(_path):
        os.environ["PATH"] = _path + os.pathsep + os.environ.get("PATH", "")

import torch
import config
from core.brain import process_user_input, check_context_health
import tools.sentry as sentry
from tools.discord_listener import start_discord_dm_listener
# from tools.realtime_stream import (
#     publish_telemetry,
#     publish_terminal,
#     register_stream_client,
#     stream_dispatch_loop,
#     unregister_stream_client,
# )
from tools.vision import load_known_faces, capture_screen_base64
from tools.voice_recognition import load_known_voices
import tools.voice_recognition as _voice_rec
import webrtc_bridge
import tools.diagnostics as _diagnostics
import tools.gesture as _gesture_mod
import tools.face_server as _face_server
import tools.intervention as _intervention
import tools.awareness as _awareness
_diagnostics.install()


LIVEKIT_ROOM_NAME = os.getenv("LIVEKIT_ROOM", "aster-command-center")

# Verify the llama-server engine is reachable (60000-token context, mmproj vision)
try:
    config.load_engine()
except Exception as e:
    print(f"[Aster Core] CRITICAL: Failed to load llama-cpp-python engine: {e}")



# ============================================================================
# FASTAPI / DASHBOARD — DISABLED (headless terminal mode)
# ============================================================================
# app = FastAPI(title="Aster Command Center API", version="1.0.0")
# stream_dispatch_task: asyncio.Task | None = None
# telemetry_stream_task: asyncio.Task | None = None
# app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
#                    allow_methods=["*"], allow_headers=["*"])
# @app.get("/api/token") def get_livekit_token(): ...
# @app.get("/api/telemetry") def get_telemetry(): ...
# @app.on_event("startup") async def on_startup(): ...
# @app.on_event("shutdown") async def on_shutdown(): ...
# @app.websocket("/api/stream") async def websocket_stream(): ...


# ============================================================================
# SHARED TELEGRAM RESPONSE DISPATCHER
# ============================================================================
def _dispatch_telegram_response(message, response_text):
    """Intercepts audio payload tags, sends voice files or text as appropriate."""
    import re as _re

    if not response_text or not response_text.strip():
        response_text = "[No response generated.]"

    # Audio Payload Interceptor & Cleanup
    if "[NATIVE_AUDIO_PAYLOAD:" in response_text:
        match = _re.search(r'\[NATIVE_AUDIO_PAYLOAD:(.*?)\]', response_text)
        if match:
            audio_path = match.group(1)

            # Guard against hallucinated payload tags (LLM fabricating the format)
            if os.path.exists(audio_path):
                try:
                    config.bot.send_chat_action(message.chat.id, 'record_voice')
                    with open(audio_path, 'rb') as voice_file:
                        config.bot.send_voice(message.chat.id, voice_file)
                    os.remove(audio_path)
                    print(f"[Aster Internal: Audio transmitted and local file deleted.]")
                except Exception as e:
                    print(f"[Telegram Error: Failed to send voice note - {e}]")
                # Audio-only: suppress text when voice note is sent
                response_text = ""
            else:
                # Hallucinated tag — strip it and keep the text
                print(f"[Aster Internal: Hallucinated audio payload detected — path '{audio_path}' does not exist. Stripping tag.]")
                response_text = _re.sub(r'\[NATIVE_AUDIO_PAYLOAD:.*?\]\s*', '', response_text).strip()

    # Send remaining text (if any)
    if response_text:
        config.bot.reply_to(message, response_text)


# ============================================================================
# TELEGRAM BOT LISTENER
# ============================================================================
if config.bot:
    @config.bot.message_handler(commands=['status'])
    def handle_status_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        config.bot.send_chat_action(message.chat.id, 'typing')
        status_text = check_context_health()
        config.bot.reply_to(message, status_text)

    @config.bot.message_handler(commands=['compact'])
    def handle_compact_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        config.bot.send_chat_action(message.chat.id, 'typing')
        result = process_user_input("/compact", None)
        config.bot.reply_to(message, result)

    @config.bot.message_handler(commands=['stop'])
    def handle_stop_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        if config.brain_lock.locked():
            config.STOP_REQUESTED = True
            config.bot.reply_to(message, "Stop signal sent. Aster will halt after the current tool completes.")
        else:
            config.bot.reply_to(message, "Nothing is running.")

    @config.bot.message_handler(commands=['diagnostics'])
    def handle_diagnostics_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        if len(args) > 1 and args[1].lower() == "on":
            config.DIAGNOSTICS_MODE = True
            config.bot.reply_to(message, "[Aster Diagnostics: ACTIVE. Debug output will be forwarded here.]")
        else:
            config.DIAGNOSTICS_MODE = False
            config.bot.reply_to(message, "[Aster Diagnostics: INACTIVE.]")

    @config.bot.message_handler(commands=['screenshot'])
    def handle_screenshot_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        config.bot.send_chat_action(message.chat.id, 'upload_photo')
        try:
            import io
            img_bytes = base64.b64decode(capture_screen_base64(max_width=0))
            config.bot.send_photo(message.chat.id, io.BytesIO(img_bytes), caption="[Aster: Live screen capture]")
        except Exception as e:
            config.bot.reply_to(message, f"[Aster: Screenshot failed — {e}]")

    @config.bot.message_handler(commands=['peek'])
    def handle_peek_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        n = 10
        if len(args) > 1:
            try:
                n = max(1, min(int(args[1]), 40))
            except ValueError:
                pass
        try:
            import html
            import core.brain as _brain
            history = _brain.messages
            tail = history[-n:]
            lines = []
            for idx, msg in enumerate(tail, start=len(history) - len(tail)):
                role = msg.get("role", "?")
                content = msg.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for p in content:
                        if p.get("type") == "image_url":
                            parts.append("[IMG]")
                        elif p.get("type") == "text":
                            parts.append(p.get("text", "")[:120])
                    text = " | ".join(parts)
                else:
                    text = str(content)
                text = text[:200].replace("\n", " ")
                lines.append(f"[{idx}] {role}: {text}")
            header = f"Messages {len(history)-len(tail)}–{len(history)-1} of {len(history)} total:\n\n"
            body = "\n".join(lines)
            output = header + body
            MAX_CHUNK = 3800
            first = True
            while output:
                chunk, output = output[:MAX_CHUNK], output[MAX_CHUNK:]
                config.bot.send_message(
                    message.chat.id,
                    f"<pre>{html.escape(chunk)}</pre>",
                    parse_mode="HTML",
                    reply_to_message_id=message.message_id if first else None,
                )
                first = False
        except Exception as e:
            config.bot.reply_to(message, f"[Aster: /peek failed — {e}]")

    @config.bot.message_handler(commands=['gpu'])
    def handle_gpu_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        import subprocess
        try:
            # nvidia-smi reports TRUE board usage — torch.cuda.* only sees
            # PyTorch's own allocations and misses llama-server + CTranslate2.
            board = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,memory.used,memory.total,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if board.returncode != 0 or not board.stdout.strip():
                raise RuntimeError(board.stderr.strip() or "nvidia-smi unavailable")

            name, used, total, free = [
                p.strip() for p in board.stdout.strip().splitlines()[0].split(",")
            ]
            used_gb = float(used) / 1024
            total_gb = float(total) / 1024
            free_gb = float(free) / 1024
            pct = (used_gb / total_gb * 100) if total_gb else 0

            config.bot.reply_to(message, (
                f"[Aster GPU: {name}]\n"
                f"Used : {used_gb:.2f} / {total_gb:.2f} GB  ({pct:.0f}%)\n"
                f"Free : {free_gb:.2f} GB"
            ))
        except Exception as e:
            # Fallback: PyTorch-only view (undercounts — excludes llama-server/Whisper)
            try:
                if torch.cuda.is_available():
                    props = torch.cuda.get_device_properties(0)
                    reserved = torch.cuda.memory_reserved(0) / 1024**3
                    config.bot.reply_to(message, (
                        f"[Aster GPU: {props.name} — nvidia-smi unavailable]\n"
                        f"PyTorch reserved: {reserved:.2f} GB\n"
                        f"(excludes llama-server & Whisper — install nvidia-smi for true usage)"
                    ))
                else:
                    config.bot.reply_to(message, f"[Aster GPU: query failed — {e}]")
            except Exception as e2:
                config.bot.reply_to(message, f"[Aster GPU: query failed — {e2}]")

    @config.bot.message_handler(content_types=['voice'])
    def handle_voice_message(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return

        config.bot.send_chat_action(message.chat.id, 'record_voice')

        import subprocess
        temp_audio_path = "temp_telegram_voice.ogg"
        temp_pcm_path = "temp_telegram_voice_pcm.wav"
        try:
            # 1. Download the Telegram voice note
            file_info = config.bot.get_file(message.voice.file_id)
            downloaded_file = config.bot.download_file(file_info.file_path)

            with open(temp_audio_path, 'wb') as new_file:
                new_file.write(downloaded_file)

            # 2. Transcode .ogg -> 16kHz mono PCM WAV for Faster-Whisper
            print("\n[Aster Internal: Processing incoming voice transmission via Whisper...]")
            subprocess.run(
                ["ffmpeg", "-y", "-i", temp_audio_path,
                 "-ar", "16000", "-ac", "1", "-f", "wav", temp_pcm_path],
                capture_output=True, check=True,
            )
            with open(temp_pcm_path, "rb") as f:
                pcm_bytes = f.read()

            # 3. Identify speaker. Unknown voices are processed normally but the
            #    temp WAV is stashed so /enroll <name> can save it later.
            try:
                from tools.voice_recognition import identify_and_maybe_learn
                speaker = identify_and_maybe_learn(pcm_bytes)
            except Exception:
                speaker = None

            if speaker is None:
                with open(_voice_rec.TEMP_VOICE_PATH, "wb") as vf:
                    vf.write(pcm_bytes)

            # 4. Transcribe the clip, then fuse prosody + transcript content into
            #    the [Mood:] tag — the same shape the LiveKit path produces in
            #    local_stt.py. The engine has no audio input, so the transcript IS
            #    the turn.
            from local_stt import transcribe_file, format_transcript
            transcript = transcribe_file(temp_pcm_path)
            if not transcript:
                _dispatch_telegram_response(
                    message,
                    "I could not make out any speech in that voice note, Sir.",
                )
                return
            mood = None
            try:
                from tools.emotion_recognition import (
                    detect_voice_emotion, detect_text_emotion, fuse_moods,
                )
                mood = fuse_moods(detect_text_emotion(transcript),
                                  detect_voice_emotion(pcm_bytes))
            except Exception:
                pass

            caption = message.caption if message.caption else ""
            user_text = format_transcript(transcript, speaker=speaker, mood=mood)
            if caption:
                user_text += f"\n\n[User caption: {caption}]"
            response_text = process_user_input(user_text, None)
            if speaker is None:
                response_text = (response_text or "").rstrip() + \
                    "\n\n_(Voice unrecognised — use /enroll <name> to save this profile.)_"
            _dispatch_telegram_response(message, response_text)

        except Exception as e:
            config.bot.reply_to(message, f"[Aster: Failed to process audio signal: {str(e)}]")
        finally:
            if os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)
            if os.path.exists(temp_pcm_path):
                os.remove(temp_pcm_path)

    @config.bot.message_handler(content_types=['photo'])
    def handle_photo_message(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            return

        config.bot.send_chat_action(message.chat.id, 'typing')

        try:
            # 1. Download the highest resolution version of the Telegram photo
            file_info = config.bot.get_file(message.photo[-1].file_id)
            downloaded_file = config.bot.download_file(file_info.file_path)

            # 2. Convert raw image directly to Base64
            img_base64 = base64.b64encode(downloaded_file).decode('utf-8')

            # 3. Extract the user's caption
            caption = message.caption if message.caption else "Look at this image."

            # 4. Route to Brain using a strict payload flag
            response_text = process_user_input(f"[NATIVE_IMAGE_PAYLOAD:{img_base64}]{caption}", None)

            if not response_text or not response_text.strip():
                response_text = "[No response generated.]"

            # 5. Send Aster's response
            config.bot.reply_to(message, response_text)

        except Exception as e:
            config.bot.reply_to(message, f"[Aster: Failed to process visual signal: {str(e)}]")

    @config.bot.message_handler(commands=['sentry'])
    def handle_sentry_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID: return

        args = message.text.split()
        if len(args) > 1 and args[1].lower() == "on":
            _gesture_mod.GESTURE_ACTIVE = False  # mutual exclusion
            sentry.SENTRY_ACTIVE = True
            config.bot.reply_to(message, "[Aster Sentry: ACTIVATED. Polling every 5 seconds.]")
        else:
            sentry.SENTRY_ACTIVE = False
            config.bot.reply_to(message, "[Aster Sentry: DEACTIVATED.]")

    @config.bot.message_handler(commands=['gesture'])
    def handle_gesture_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        if len(args) > 1 and args[1].lower() == "on":
            sentry.SENTRY_ACTIVE = False          # mutual exclusion
            _gesture_mod.GESTURE_ACTIVE = True
            config.bot.reply_to(message, (
                "[Aster Gesture: ACTIVE. Sentry disabled.]\n"
                "Gestures: index=volume | palm=play⦸pause | fist=pause | "
                "swipe=skip⦸prev | thumbs↓=hide | V=lock"
            ))
        else:
            _gesture_mod.GESTURE_ACTIVE = False
            config.bot.reply_to(message, "[Aster Gesture: INACTIVE.]")

    @config.bot.message_handler(commands=['intervention'])
    def handle_intervention_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        if len(args) > 1 and args[1].lower() == "on":
            _intervention.toggle_intervention(True)
            config.bot.reply_to(message, (
                "[Aster Intervention: ACTIVE. Watching focus on distracting apps.]"
            ))
        else:
            _intervention.toggle_intervention(False)
            config.bot.reply_to(message, "[Aster Intervention: INACTIVE.]")

    @config.bot.message_handler(commands=['moodactions'])
    def handle_moodactions_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        state = len(args) > 1 and args[1].lower() == "on"
        config.MOOD_ACTIONS_ENABLED = state
        status = "ACTIVE. Will OFFER mood-fitting actions while chatting." if state else "INACTIVE."
        config.bot.reply_to(message, f"[Aster Mood Actions: {status}]")

    @config.bot.message_handler(commands=['checkins'])
    def handle_checkins_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        state = len(args) > 1 and args[1].lower() == "on"
        config.MOOD_CHECKIN_ENABLED = state
        status = "ACTIVE. Will check in if a mood persists then you go quiet." if state else "INACTIVE."
        config.bot.reply_to(message, f"[Aster Mood Check-ins: {status}]")

    @config.bot.message_handler(commands=['faceemotion'])
    def handle_faceemotion_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        state = len(args) > 1 and args[1].lower() == "on"
        config.FACE_EMOTION_ENABLED = state
        if state:
            try:
                from tools.emotion_recognition import load_face_emotion_model
                load_face_emotion_model()
            except Exception as e:
                print(f"[Aster] Face emotion load on enable failed: {e}")
        status = "ACTIVE. Reading facial expression from the webcam." if state else "INACTIVE."
        config.bot.reply_to(message, f"[Aster Face Emotion: {status}]")

    @config.bot.message_handler(commands=['ambientaudio'])
    def handle_ambientaudio_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        state = len(args) > 1 and args[1].lower() == "on"
        config.AMBIENT_AUDIO_ENABLED = state
        if not state:
            try:
                from tools.emotion_recognition import reset_ambient_voice_mood
                reset_ambient_voice_mood()
            except Exception:
                pass
        status = f"ACTIVE. Passively listening to the room for {config.OWNER_NAME}'s vocal tone." if state else "INACTIVE."
        config.bot.reply_to(message, f"[Aster Ambient Audio: {status}]")

    @config.bot.message_handler(commands=['initiative'])
    def handle_initiative_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        args = message.text.split()
        if len(args) < 2:
            config.bot.reply_to(message, (
                f"[Aster Initiative: level {config.INITIATIVE_LEVEL}/3. "
                f"Usage: /initiative <0-3> or /initiative more|less]"
            ))
            return
        result = _awareness.set_initiative(args[1])
        config.bot.reply_to(message, result)

    @config.bot.message_handler(commands=['note'])
    def handle_note_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        parts = message.text.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            config.bot.reply_to(message, "[Aster: Usage: /note <your note text>]")
            return
        from tools.notes import save_note
        config.bot.reply_to(message, save_note(parts[1].strip()))

    @config.bot.message_handler(commands=['find'])
    def handle_find_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        parts = message.text.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            config.bot.reply_to(message, "[Aster: Usage: /find <thing on screen>]")
            return
        goal = parts[1].strip()
        from tools.vision import locate_ui_elements_boxed
        from tools.assist import highlight_regions
        matches = locate_ui_elements_boxed(goal)
        if not matches:
            config.bot.reply_to(message, f'[Aster: Could not find "{goal}" on screen.]')
            return
        highlight_regions(matches)
        config.bot.reply_to(message, f'[Aster: Highlighting {len(matches)} match(es) for "{goal}".]')

    @config.bot.message_handler(commands=['enroll'])
    def handle_enroll_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        parts = message.text.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            config.bot.reply_to(message, "[Aster: Usage: /enroll <name>]")
            return
        name = parts[1].strip().title()
        speaker_dir = os.path.join(config.VOICES_DIR, name)
        try:
            if os.path.exists(_voice_rec.TEMP_VOICE_PATH):
                os.makedirs(speaker_dir, exist_ok=True)
                base_wav = os.path.join(speaker_dir, f"{name}.wav")
                if os.path.exists(base_wav):
                    # Re-enrolling — add as an additional sample instead of overwriting.
                    import time as _time
                    dest = os.path.join(speaker_dir, f"{name}__{int(_time.time())}.wav")
                    msg_suffix = f"Additional sample added for '{name}'."
                else:
                    dest = base_wav
                    msg_suffix = f"Voice profile for '{name}' saved."
                shutil.move(_voice_rec.TEMP_VOICE_PATH, dest)
                load_known_voices()
                config.bot.reply_to(message, f"[Aster: {msg_suffix} I'll recognise that voice next time, Sir.]")
            else:
                config.bot.reply_to(message, "[Aster: No pending voice recording found. Please send a voice note first.]")
        except Exception as e:
            config.bot.reply_to(message, f"[Aster: Failed to save voice profile — {e}]")

    @config.bot.message_handler(commands=['unmute'])
    def handle_unmute_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        parts = message.text.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            config.bot.reply_to(message, "[Aster: Usage: /unmute <name>]")
            return
        name = parts[1].strip()
        try:
            import tools.conversations as _conv
            record = _conv.get_state(name)
            if not record.get("mute_count") and not record.get("strikes"):
                config.bot.reply_to(message, f"[Aster: No record for '{name}'.]")
                return
            _conv.clear_mute(name)
            config.bot.reply_to(message, (
                f"[Aster: '{name}' may speak to me again. "
                f"(They have been silenced {record.get('mute_count', 0)} time(s) before.)]"
            ))
        except Exception as e:
            config.bot.reply_to(message, f"[Aster: Could not unmute '{name}' — {e}]")

    @config.bot.message_handler(commands=['discordlog'])
    def handle_discordlog_command(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return
        parts = message.text.split(None, 1)
        name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        try:
            import tools.conversations as _conv
            config.bot.reply_to(message, _conv.incidents(name))
        except Exception as e:
            config.bot.reply_to(message, f"[Aster: Could not read the Discord record — {e}]")

    @config.bot.message_handler(func=lambda message: True)
    def handle_telegram_message(message):
        if message.chat.id != config.AUTHORIZED_CHAT_ID:
            config.bot.reply_to(message, "Unauthorized user. Access denied.")
            return

        # Intruder Naming Interceptor
        if sentry.WAITING_FOR_ID:
            raw_text = message.text.strip()

            # LLM name extraction: pull the proper noun from conversational input
            try:
                from core.brain import _execute_llm_completion
                extraction_prompt = (
                    f"Extract ONLY the person's name from this message. "
                    f"If the message is a first-person pronoun like 'It's me', 'me', or 'I', return the name '{config.OWNER_NAME}'. "
                    f"Return ONLY the exact name, with no punctuation or extra words. "
                    f"Message: '{raw_text}'"
                )
                clean_name = _execute_llm_completion(
                    messages=[{"role": "user", "content": extraction_prompt}],
                    temperature=0.1,
                    n_predict=20,
                ).get("content") or ""

                # The Ironclad Fallback: check for empty string FIRST
                if not clean_name or len(clean_name) > 20 or " " in clean_name.strip():
                    if "me" in raw_text.lower() or " i " in f" {raw_text.lower()} ":
                        clean_name = config.OWNER_NAME
                    else:
                        clean_name = raw_text.split()[-1].title()

            except Exception as e:
                print(f"[Sentry Error] LLM name extraction failed: {e}")
                # Failsafe without LLM
                if "me" in raw_text.lower() or " i " in f" {raw_text.lower()} ":
                    clean_name = config.OWNER_NAME
                else:
                    clean_name = raw_text.split()[-1].title()

            # Absolute zero-crash failsafe
            if not clean_name:
                clean_name = "Unknown_Intruder"

            new_name = clean_name.title()
            temp_path = os.path.join(config.VAULT_DIR, "temp_intruder.jpg")
            new_path = os.path.join(config.FACES_DIR, f"{new_name}.jpg")

            try:
                if os.path.exists(temp_path):
                    shutil.move(temp_path, new_path)
                    load_known_faces()  # Hot-reload the vault
                    load_known_voices()
                    config.bot.reply_to(message, f"[Aster: Biometric profile for '{new_name}' saved to vault. Resuming Sentry Mode.]")
                else:
                    config.bot.reply_to(message, "[Aster: Temporary image lost. Resuming Sentry Mode.]")
            except Exception as e:
                config.bot.reply_to(message, f"[Aster: Failed to save profile - {e}]")

            sentry.WAITING_FOR_ID = False
            return # Do not pass this text to the LLM brain

        # --- EXISTING BRAIN ROUTING ---
        config.bot.send_chat_action(message.chat.id, 'typing')
        response_text = process_user_input(message.text, None)

        # Send response (shared interceptor handles audio payloads)
        _dispatch_telegram_response(message, response_text)


def start_telegram_bot():
    if config.TELEGRAM_AVAILABLE:
        print("[Aster Network] Secure Telegram bridge active.")
        config.bot.infinity_polling()


def start_discord_bot_listener():
    start_discord_dm_listener()


def start_webrtc_bridge():
    print("[Aster Network] LiveKit WebRTC bridge active.")
    try:
        webrtc_bridge.start_agent_background()
    except Exception as e:
        print(f"[Aster Network] WebRTC bridge stopped: {e}")


def start_cli_loop():
    while True:
        try:
            user_input = input("\nUser: ")
        except (EOFError, KeyboardInterrupt):
            print("\n[Aster Core] CLI loop stopped.")
            break

        if not str(user_input).strip():
            continue

        process_user_input(user_input, None)


def start_background_services():
    threading.Thread(target=start_telegram_bot, daemon=True).start()
    threading.Thread(target=start_discord_bot_listener, daemon=True).start()
    threading.Thread(target=start_webrtc_bridge, daemon=True).start()

    # Start Sentry daemon
    sentry.telegram_bot = config.bot
    sentry.chat_id = config.AUTHORIZED_CHAT_ID
    # threading.Thread(target=sentry.sentry_daemon, daemon=True).start()  # TEMP: disabled for image pipeline debugging

    # Start Gesture daemon (inactive until /gesture on)
    threading.Thread(target=_gesture_mod.gesture_daemon, daemon=True).start()

    # Start Intervention daemon (inactive until /intervention on)
    threading.Thread(target=_intervention.intervention_daemon, daemon=True).start()

    # Start Awareness daemon (active by default — ambient context + initiative dial)
    threading.Thread(target=_awareness.awareness_daemon, daemon=True, name="awareness").start()

    # Start Ambient Audio daemon (inactive until /ambientaudio on — no mic opened while idle)
    from tools import ambient_audio as _ambient_audio
    threading.Thread(target=_ambient_audio.ambient_audio_daemon, daemon=True, name="ambient-audio").start()

    # Start Face server (token + log stream for the Tauri desktop UI)
    threading.Thread(target=_face_server.start_face_server, daemon=True).start()

    # Start llama-server health watchdog (alerts + capped auto-restart)
    from tools import health_watchdog as _health_watchdog
    threading.Thread(target=_health_watchdog.health_watchdog_daemon, daemon=True, name="health-watchdog").start()

    # Start Aster-UI Vite dev server (http://localhost:5173)
    _ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Aster-UI")
    if os.path.isdir(_ui_dir):
        subprocess.Popen(
            "npm run dev",
            cwd=_ui_dir,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        print("[Aster UI] Dashboard starting at http://localhost:5173")
    else:
        print("[Aster UI] Aster-UI directory not found — skipping dashboard.")

    # CLI loop runs on the main thread (below) — no daemon needed.


# ============================================================================
# PRELOAD MODEL INTO MEMORY
# ============================================================================
print("[Aster Core] Engine already loaded via config.load_engine().")
try:
    # Warm up the native REST endpoint with a tiny completion
    import requests as _requests
    _warmup_resp = _requests.post(
        "http://localhost:8080/completion",
        json={"prompt": ".", "n_predict": 1, "temperature": 0.1},
        timeout=10,
    )
    if _warmup_resp.status_code == 200:
        print("[Aster Core] Text engine warmed up via native REST endpoint.")
    else:
        print(f"[Aster Core] WARNING: Warmup returned status {_warmup_resp.status_code}")
except Exception as e:
    print(f"[Aster Core] Warning: Engine warmup failed: {e}")

# Prewarm the Laya System-1 kernel in the background (QA round 3): it loads lazily on
# first use, so without this the FIRST kernel-touching action after every restart pays
# the ~35 s cold load (a GUI click, a Discord message, or a spoken utterance). Note the
# prewarm only keeps the kernel hot when `automation.laya_keep_resident: true`; with the
# code default (false) it warms the OS page cache but the model still unloads at
# refcount 0 (QA round 7).
try:
    if getattr(config, "USE_LAYA_KERNEL", False):
        def _prewarm_laya():
            try:
                import core.system1 as _s1
                _s1.acquire()
                _s1.release()
                print("[Aster Core] Laya kernel prewarmed.")
            except Exception as _e:
                print(f"[Aster Core] Laya prewarm skipped: {_e}")
        import threading as _threading
        _threading.Thread(target=_prewarm_laya, name="laya-prewarm", daemon=True).start()
except Exception as e:
    print(f"[Aster Core] Laya prewarm setup skipped: {e}")

# ============================================================================
# MAIN BOOTSTRAP
# ============================================================================
if __name__ == "__main__":
    start_background_services()
    print("[Aster Core] All systems online. Entering CLI mode.")
    start_cli_loop()  # blocks on main thread — keeps daemon threads alive
