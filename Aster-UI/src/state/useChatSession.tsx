/**
 * useChatSession — session-level context that keeps chat messages and call state
 * alive across tab switches. Lives above BodyRouter so VoiceChat never loses its
 * local state when it unmounts/remounts via AnimatePresence.
 */
import {
  createContext,
  useContext,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from "react";
import type { ChatMessage } from "../types";

interface ChatSessionCtx {
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  streaming: boolean;
  setStreaming: Dispatch<SetStateAction<boolean>>;
  onCall: boolean;
  setOnCall: Dispatch<SetStateAction<boolean>>;
  muted: boolean;
  setMuted: Dispatch<SetStateAction<boolean>>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  roomRef: MutableRefObject<any>;
}

const ChatSessionContext = createContext<ChatSessionCtx | null>(null);

export function ChatSessionProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [onCall, setOnCall] = useState(false);
  const [muted, setMuted] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const roomRef = useRef<any>(null);

  return (
    <ChatSessionContext.Provider
      value={{ messages, setMessages, streaming, setStreaming, onCall, setOnCall, muted, setMuted, roomRef }}
    >
      {children}
    </ChatSessionContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useChatSession(): ChatSessionCtx {
  const ctx = useContext(ChatSessionContext);
  if (!ctx) throw new Error("useChatSession must be used inside <ChatSessionProvider>");
  return ctx;
}

// Non-throwing variant for consumers that can work without a chat session
// (e.g. AsterMoodProvider when AppShell is rendered standalone in tests).
// eslint-disable-next-line react-refresh/only-export-components
export function useOptionalChatSession(): ChatSessionCtx | null {
  return useContext(ChatSessionContext);
}
