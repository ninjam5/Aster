import { type MutableRefObject } from "react";
import { ASTER_WS_BASE } from "./config";
import { asterGet } from "./client";

interface TokenResponse {
  token: string;
  ws_url: string;
  room: string;
}

interface LocalWsSession {
  kind: "local_ws";
  ws: WebSocket;
  audioCtx: AudioContext;
  mediaStream: MediaStream;
  processor: ScriptProcessorNode;
  sourceNode: MediaStreamAudioSourceNode;
  activeSources: Set<AudioBufferSourceNode>;
  nextPlayTime: number;
  sampleRateOut: number;
  muted: boolean;
  disconnect: () => Promise<void>;
  setMuted: (muted: boolean) => Promise<void>;
}

// Marks the hidden <audio> elements we append for the benched LiveKit path.
const REMOTE_AUDIO_ATTR = "data-aster-remote-audio";

function removeRemoteAudio() {
  document
    .querySelectorAll(`audio[${REMOTE_AUDIO_ATTR}]`)
    .forEach((el) => el.remove());
}

/**
 * Downsample Float32 audio from AudioContext's native sample rate to 16 kHz mono
 * int16 PCM with box-averaging anti-alias filter.
 */
function downsampleTo16kInt16(input: Float32Array, inRate: number): Int16Array {
  if (inRate === 16000) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }
  const ratio = inRate / 16000;
  const outLength = Math.floor(input.length / ratio);
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j++) {
      sum += input[j];
      count++;
    }
    const avg = count > 0 ? sum / count : input[start] ?? 0;
    const s = Math.max(-1, Math.min(1, avg));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/**
 * useLiveKitCall — mainstream voice hook now powered by Direct Local WebSocket
 * audio (`ws://127.0.0.1:8000/api/voice/ws`). LiveKit room logic is kept
 * benched below (`startLiveKitCall`) for future mobile app use.
 *
 * Accepts an external roomRef (from useChatSession) so the active audio session
 * survives component unmounts (e.g. switching tabs).
 */
export function useLiveKitCall(roomRef: MutableRefObject<unknown>) {
  async function startCall(
    onStateChange: (onCall: boolean) => void,
    onError: (msg: string) => void,
  ) {
    try {
      if (roomRef.current) {
        await endCall(onStateChange);
      }

      const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });

      const audioCtx = new AudioContext();
      await audioCtx.resume();

      const ws = new WebSocket(`${ASTER_WS_BASE}/api/voice/ws`);
      ws.binaryType = "arraybuffer";

      const sourceNode = audioCtx.createMediaStreamSource(mediaStream);
      const processor = audioCtx.createScriptProcessor(2048, 1, 1);
      const silentSink = audioCtx.createGain();
      silentSink.gain.value = 0;

      const activeSources = new Set<AudioBufferSourceNode>();
      let cleanedUp = false;

      const session: LocalWsSession = {
        kind: "local_ws",
        ws,
        audioCtx,
        mediaStream,
        processor,
        sourceNode,
        activeSources,
        nextPlayTime: 0,
        sampleRateOut: 24000,
        muted: false,
        async disconnect() {
          if (cleanedUp) return;
          cleanedUp = true;
          try {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "hangup" }));
            }
          } catch {
            // ignore
          }
          try {
            ws.close();
          } catch {
            // ignore
          }
          for (const src of activeSources) {
            try {
              src.stop();
              src.disconnect();
            } catch {
              // ignore
            }
          }
          activeSources.clear();
          try {
            processor.onaudioprocess = null;
            processor.disconnect();
            sourceNode.disconnect();
            silentSink.disconnect();
          } catch {
            // ignore
          }
          mediaStream.getTracks().forEach((t) => t.stop());
          try {
            await audioCtx.close();
          } catch {
            // ignore
          }
        },
        async setMuted(muted: boolean) {
          session.muted = muted;
          mediaStream.getAudioTracks().forEach((t) => {
            t.enabled = !muted;
          });
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "mute", muted }));
          }
        },
      };

      const clearPlaybackQueue = () => {
        for (const src of activeSources) {
          try {
            src.stop();
            src.disconnect();
          } catch {
            // ignore
          }
        }
        activeSources.clear();
        session.nextPlayTime = audioCtx.currentTime;
      };

      ws.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          const byteLen = event.data.byteLength;
          if (byteLen < 2) return;
          const pcm16 = new Int16Array(event.data, 0, Math.floor(byteLen / 2));
          const float32 = new Float32Array(pcm16.length);
          for (let i = 0; i < pcm16.length; i++) {
            float32[i] = pcm16[i] / 32768.0;
          }
          const audioBuffer = audioCtx.createBuffer(
            1,
            float32.length,
            session.sampleRateOut,
          );
          audioBuffer.copyToChannel(float32, 0);

          const src = audioCtx.createBufferSource();
          src.buffer = audioBuffer;
          src.connect(audioCtx.destination);

          const now = audioCtx.currentTime;
          if (session.nextPlayTime < now) {
            session.nextPlayTime = now + 0.015;
          }
          src.start(session.nextPlayTime);
          session.nextPlayTime += audioBuffer.duration;
          activeSources.add(src);
          src.onended = () => {
            activeSources.delete(src);
          };
          return;
        }

        if (typeof event.data === "string") {
          try {
            const msg = JSON.parse(event.data) as {
              type?: string;
              sample_rate_out?: number;
            };
            if (msg.type === "ready" && msg.sample_rate_out) {
              session.sampleRateOut = msg.sample_rate_out;
            } else if (msg.type === "clear_audio") {
              clearPlaybackQueue();
            }
          } catch {
            // ignore malformed JSON
          }
        }
      };

      await new Promise<void>((resolve, reject) => {
        const onOpen = () => {
          ws.removeEventListener("error", onErr);
          resolve();
        };
        const onErr = () => {
          ws.removeEventListener("open", onOpen);
          reject(new Error("Could not connect to local voice WebSocket (/api/voice/ws)."));
        };
        ws.addEventListener("open", onOpen, { once: true });
        ws.addEventListener("error", onErr, { once: true });
      });

      ws.onclose = () => {
        void session.disconnect();
        if (roomRef.current === session) {
          roomRef.current = null;
        }
        onStateChange(false);
      };

      processor.onaudioprocess = (e: AudioProcessingEvent) => {
        if (session.muted || ws.readyState !== WebSocket.OPEN) return;
        const input = e.inputBuffer.getChannelData(0);
        const pcm16 = downsampleTo16kInt16(input, audioCtx.sampleRate);
        if (pcm16.length > 0) {
          ws.send(pcm16.buffer);
        }
      };

      sourceNode.connect(processor);
      processor.connect(silentSink);
      silentSink.connect(audioCtx.destination);

      roomRef.current = session;
      onStateChange(true);
    } catch (err) {
      onError(String(err));
    }
  }

  // Benched LiveKit WebRTC room starter — preserved for future mobile app use.
  async function startLiveKitCall(
    onStateChange: (onCall: boolean) => void,
    onError: (msg: string) => void,
  ) {
    try {
      const { token, ws_url } = await asterGet<TokenResponse>("/api/token");

      const { Room, RoomEvent, Track } = await import("livekit-client");
      const room = new Room();
      roomRef.current = room;

      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) {
          const el = track.attach();
          el.setAttribute(REMOTE_AUDIO_ATTR, "1");
          el.style.display = "none";
          document.body.appendChild(el);
        }
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => el.remove());
      });

      room.on(RoomEvent.Disconnected, () => {
        removeRemoteAudio();
        onStateChange(false);
        roomRef.current = null;
      });

      await room.connect(ws_url, token);
      await room.localParticipant.setMicrophoneEnabled(true);
      await room.startAudio();
      onStateChange(true);
    } catch (err) {
      onError(String(err));
    }
  }

  async function endCall(onStateChange: (onCall: boolean) => void) {
    if (roomRef.current) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const current = roomRef.current as any;
      if (typeof current.disconnect === "function") {
        await current.disconnect();
      }
      roomRef.current = null;
    }
    removeRemoteAudio();
    onStateChange(false);
  }

  async function setMuted(muted: boolean) {
    if (!roomRef.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const current = roomRef.current as any;
    if (typeof current.setMuted === "function") {
      await current.setMuted(muted);
    } else if (current.localParticipant) {
      await current.localParticipant.setMicrophoneEnabled(!muted);
    }
  }

  return { startCall, startLiveKitCall, endCall, setMuted };
}
