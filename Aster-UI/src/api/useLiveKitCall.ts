import { type MutableRefObject } from "react";
import { asterGet } from "./client";

interface TokenResponse {
  token: string;
  ws_url: string;
  room: string;
}

// Marks the hidden <audio> elements we append for the agent's voice, so they can
// be cleaned up on disconnect.
const REMOTE_AUDIO_ATTR = "data-aster-remote-audio";

function removeRemoteAudio() {
  document
    .querySelectorAll(`audio[${REMOTE_AUDIO_ATTR}]`)
    .forEach((el) => el.remove());
}

/**
 * useLiveKitCall — accepts an external roomRef (from useChatSession) so the
 * LiveKit Room instance survives component unmounts (e.g. switching tabs).
 * Previously used a closure-local `let room`, which reset to null on every render
 * and broke endCall.
 */
export function useLiveKitCall(roomRef: MutableRefObject<unknown>) {
  async function startCall(
    onStateChange: (onCall: boolean) => void,
    onError: (msg: string) => void,
  ) {
    try {
      const { token, ws_url } = await asterGet<TokenResponse>("/api/token");

      const { Room, RoomEvent, Track } = await import("livekit-client");
      const room = new Room();
      roomRef.current = room;

      // Attach Aster's remote audio track so it actually plays. Without this the
      // agent publishes its voice but the browser never renders it — the call
      // connects and transcribes, yet you hear nothing.
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
      // Unlock browser audio inside the click gesture so Aster's later replies
      // are permitted to play (autoplay policy otherwise blocks them).
      await room.startAudio();
      onStateChange(true);
    } catch (err) {
      onError(String(err));
    }
  }

  async function endCall(onStateChange: (onCall: boolean) => void) {
    if (roomRef.current) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await (roomRef.current as any).disconnect();
      roomRef.current = null;
    }
    removeRemoteAudio();
    onStateChange(false);
  }

  async function setMuted(muted: boolean) {
    if (!roomRef.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await (roomRef.current as any).localParticipant.setMicrophoneEnabled(!muted);
  }

  return { startCall, endCall, setMuted };
}
