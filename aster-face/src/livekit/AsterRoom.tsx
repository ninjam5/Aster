import { useEffect, useRef } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useLocalParticipant,
  useRoomContext,
} from "@livekit/components-react";
import { RoomEvent, Track, type RemoteTrack } from "livekit-client";

// served verbatim from aster-ui/public/ — worklets must be standalone files
const RMS_WORKLET_URL = "/rms-processor.js";

type AsterRoomProps = {
  token: string;
  serverUrl: string;
  muted: boolean;
  onConnected: () => void;
  onDisconnected: () => void;
  onError: (message: string) => void;
  onLevel: (level: number) => void;
};

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

/** Direct local WebSocket audio bridge for the desktop reactor (mainstream path). */
function LocalWsAudioRoom({
  serverUrl,
  muted,
  onConnected,
  onDisconnected,
  onError,
  onLevel,
}: Omit<AsterRoomProps, "token">) {
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const onConnectedRef = useRef(onConnected);
  onConnectedRef.current = onConnected;
  const onDisconnectedRef = useRef(onDisconnected);
  onDisconnectedRef.current = onDisconnected;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const onLevelRef = useRef(onLevel);
  onLevelRef.current = onLevel;

  useEffect(() => {
    streamRef.current?.getAudioTracks().forEach((t) => {
      t.enabled = !muted;
    });
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "mute", muted }));
    }
  }, [muted]);

  useEffect(() => {
    let disposed = false;
    let audioCtx: AudioContext | null = null;
    let ws: WebSocket | null = null;
    let rafId = 0;
    const activeSources = new Set<AudioBufferSourceNode>();
    let nextPlayTime = 0;
    let sampleRateOut = 24000;

    async function run() {
      try {
        const mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: 1,
          },
        });
        if (disposed) {
          mediaStream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = mediaStream;
        mediaStream.getAudioTracks().forEach((t) => {
          t.enabled = !mutedRef.current;
        });

        audioCtx = new AudioContext();
        await audioCtx.resume();

        // Output analyser for reactor ring pulse
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        analyser.connect(audioCtx.destination);
        const timeDomain = new Float32Array(analyser.fftSize);
        let smoothed = 0;
        let lastEmit = 0;

        const tickLevel = () => {
          if (disposed) return;
          analyser.getFloatTimeDomainData(timeDomain);
          let sumSq = 0;
          for (let i = 0; i < timeDomain.length; i++) {
            sumSq += timeDomain[i] * timeDomain[i];
          }
          const rms = Math.sqrt(sumSq / timeDomain.length);
          const raw = Math.min(1, rms * 3.5);
          const k = raw > smoothed ? 0.35 : 0.06;
          smoothed += (raw - smoothed) * k;
          const now = performance.now();
          if (now - lastEmit >= 33) {
            lastEmit = now;
            onLevelRef.current(smoothed);
          }
          rafId = requestAnimationFrame(tickLevel);
        };
        rafId = requestAnimationFrame(tickLevel);

        ws = new WebSocket(serverUrl);
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;

        const clearPlaybackQueue = () => {
          if (!audioCtx) return;
          for (const src of activeSources) {
            try {
              src.stop();
              src.disconnect();
            } catch {
              // ignore
            }
          }
          activeSources.clear();
          nextPlayTime = audioCtx.currentTime;
        };

        ws.onmessage = (event: MessageEvent) => {
          if (!audioCtx || disposed) return;
          if (event.data instanceof ArrayBuffer) {
            const byteLen = event.data.byteLength;
            if (byteLen < 2) return;
            const pcm16 = new Int16Array(event.data, 0, Math.floor(byteLen / 2));
            const float32 = new Float32Array(pcm16.length);
            for (let i = 0; i < pcm16.length; i++) {
              float32[i] = pcm16[i] / 32768.0;
            }
            const buf = audioCtx.createBuffer(1, float32.length, sampleRateOut);
            buf.copyToChannel(float32, 0);

            const src = audioCtx.createBufferSource();
            src.buffer = buf;
            src.connect(analyser);

            const now = audioCtx.currentTime;
            if (nextPlayTime < now) {
              nextPlayTime = now + 0.015;
            }
            src.start(nextPlayTime);
            nextPlayTime += buf.duration;
            activeSources.add(src);
            src.onended = () => activeSources.delete(src);
            return;
          }
          if (typeof event.data === "string") {
            try {
              const msg = JSON.parse(event.data) as {
                type?: string;
                sample_rate_out?: number;
              };
              if (msg.type === "ready" && msg.sample_rate_out) {
                sampleRateOut = msg.sample_rate_out;
              } else if (msg.type === "clear_audio") {
                clearPlaybackQueue();
              }
            } catch {
              // ignore
            }
          }
        };

        ws.onopen = () => {
          if (!disposed) {
            onConnectedRef.current();
          }
        };

        ws.onerror = () => {
          if (!disposed) {
            onErrorRef.current("Could not connect to local voice WebSocket.");
          }
        };

        ws.onclose = () => {
          if (!disposed) {
            onDisconnectedRef.current();
          }
        };

        const sourceNode = audioCtx.createMediaStreamSource(mediaStream);
        const processor = audioCtx.createScriptProcessor(2048, 1, 1);
        const silentSink = audioCtx.createGain();
        silentSink.gain.value = 0;

        processor.onaudioprocess = (e: AudioProcessingEvent) => {
          if (mutedRef.current || !ws || ws.readyState !== WebSocket.OPEN || !audioCtx) return;
          const input = e.inputBuffer.getChannelData(0);
          const pcm16 = downsampleTo16kInt16(input, audioCtx.sampleRate);
          if (pcm16.length > 0) {
            ws.send(pcm16.buffer);
          }
        };

        sourceNode.connect(processor);
        processor.connect(silentSink);
        silentSink.connect(audioCtx.destination);
      } catch (err) {
        if (!disposed) {
          onErrorRef.current(err instanceof Error ? err.message : String(err));
        }
      }
    }

    void run();

    return () => {
      disposed = true;
      cancelAnimationFrame(rafId);
      onLevelRef.current(0);
      try {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "hangup" }));
        }
        ws?.close();
      } catch {
        // ignore
      }
      wsRef.current = null;
      for (const src of activeSources) {
        try {
          src.stop();
          src.disconnect();
        } catch {
          // ignore
        }
      }
      activeSources.clear();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      void audioCtx?.close();
    };
  }, [serverUrl]);

  return null;
}

/** Source-agnostic amplitude meter for Aster's voice track (analyser on the raw MediaStreamTrack). */
function AgentVoiceMeter({ onLevel }: { onLevel: (level: number) => void }) {
  const room = useRoomContext();
  const onLevelRef = useRef(onLevel);
  onLevelRef.current = onLevel;

  useEffect(() => {
    let ctx: AudioContext | null = null;
    let node: AudioWorkletNode | null = null;
    let smoothed = 0;
    let disposed = false;

    const attach = async (track: RemoteTrack) => {
      if (track.kind !== Track.Kind.Audio || node || !track.mediaStreamTrack) return;
      ctx = new AudioContext();
      await ctx.resume();
      await ctx.audioWorklet.addModule(RMS_WORKLET_URL);
      if (disposed || !ctx) return;

      const src = ctx.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
      node = new AudioWorkletNode(ctx, "rms-processor");
      const sink = ctx.createGain();
      sink.gain.value = 0;
      src.connect(node);
      node.connect(sink);
      sink.connect(ctx.destination);

      let lastEmit = 0;
      node.port.onmessage = (e: MessageEvent<number>) => {
        const raw = Math.min(1, e.data * 3.5);
        const k = raw > smoothed ? 0.35 : 0.06;
        smoothed += (raw - smoothed) * k;
        const now = performance.now();
        if (now - lastEmit >= 33) {
          lastEmit = now;
          onLevelRef.current(smoothed);
        }
      };
    };

    const onSub = (track: RemoteTrack) => void attach(track);
    room.on(RoomEvent.TrackSubscribed, onSub);
    room.remoteParticipants.forEach((p) =>
      p.audioTrackPublications.forEach((pub) => {
        if (pub.track) void attach(pub.track as RemoteTrack);
      }),
    );

    return () => {
      disposed = true;
      room.off(RoomEvent.TrackSubscribed, onSub);
      onLevelRef.current(0);
      void ctx?.close();
    };
  }, [room]);

  return null;
}

function MuteBridge({ muted }: { muted: boolean }) {
  const { localParticipant } = useLocalParticipant();
  useEffect(() => {
    void localParticipant.setMicrophoneEnabled(!muted);
  }, [muted, localParticipant]);
  return null;
}

export default function AsterRoom({
  token,
  serverUrl,
  muted,
  onConnected,
  onDisconnected,
  onError,
  onLevel,
}: AsterRoomProps) {
  if (token === "local_ws") {
    return (
      <LocalWsAudioRoom
        serverUrl={serverUrl}
        muted={muted}
        onConnected={onConnected}
        onDisconnected={onDisconnected}
        onError={onError}
        onLevel={onLevel}
      />
    );
  }

  return (
    <LiveKitRoom
      token={token}
      serverUrl={serverUrl}
      connect={true}
      audio={true}
      video={false}
      onConnected={onConnected}
      onDisconnected={onDisconnected}
      onError={(e) => onError(e.message)}
    >
      <RoomAudioRenderer />
      <MuteBridge muted={muted} />
      <AgentVoiceMeter onLevel={onLevel} />
    </LiveKitRoom>
  );
}
