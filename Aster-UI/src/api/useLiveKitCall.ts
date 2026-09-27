import { type MutableRefObject } from "react";
import { asterGet } from "./client";

interface TokenResponse {
  token: string;
  ws_url: string;
  room: string;
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

      const { Room, RoomEvent } = await import("livekit-client");
      roomRef.current = new Room();

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (roomRef.current as any).on(RoomEvent.Disconnected, () => {
        onStateChange(false);
        roomRef.current = null;
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await (roomRef.current as any).connect(ws_url, token);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await (roomRef.current as any).localParticipant.setMicrophoneEnabled(true);
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
    onStateChange(false);
  }

  async function setMuted(muted: boolean) {
    if (!roomRef.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await (roomRef.current as any).localParticipant.setMicrophoneEnabled(!muted);
  }

  return { startCall, endCall, setMuted };
}
