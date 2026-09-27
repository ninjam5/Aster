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
      // keep the node in the render graph (silent sink) so process() runs
      const sink = ctx.createGain();
      sink.gain.value = 0;
      src.connect(node);
      node.connect(sink);
      sink.connect(ctx.destination);

      let lastEmit = 0;
      node.port.onmessage = (e: MessageEvent<number>) => {
        const raw = Math.min(1, e.data * 3.5);
        // asymmetric smoothing: snap up fast, ease down slow so brief gaps
        // between words don't flicker the reactor back to rest.
        const k = raw > smoothed ? 0.35 : 0.06;
        smoothed += (raw - smoothed) * k;
        // Throttle to ~30 Hz — worklet fires at 100+ Hz but React doesn't need it
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
