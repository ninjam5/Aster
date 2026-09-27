import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Mic, MicOff, Phone, PhoneOff } from "lucide-react";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { useChatStream } from "../api/useChatStream";
import { useLiveKitCall } from "../api/useLiveKitCall";
import { useChatSession } from "../state/useChatSession";
import { easing, slideFromLeft, slideFromRight, staggerContainer, staggerItem } from "../theme/motion";

function clockTime() {
  return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// The animated "presence" orb — three concentric rings breathing at slightly
// different rates. Idle is a slow calm breath; an active call speeds all
// three up and widens the amplitude so it reads as more alive/engaged.
function PresenceOrb({ onCall }: { onCall: boolean }) {
  return (
    <div className="relative flex h-28 w-28 items-center justify-center">
      <motion.div
        className="absolute inset-0 rounded-full bg-gradient-to-br from-accent-blue/40 to-primary/10 blur-xl"
        animate={{
          scale: onCall ? [1, 1.28, 1] : [1, 1.08, 1],
          opacity: onCall ? [0.45, 0.85, 0.45] : [0.35, 0.55, 0.35],
        }}
        transition={{ duration: onCall ? 1.6 : 3.2, repeat: Infinity, ease: easing }}
      />
      <motion.div
        className="absolute h-20 w-20 rounded-full bg-gradient-to-br from-accent-blue/60 to-primary/25"
        animate={{ scale: onCall ? [1, 1.18, 1] : [1, 1.05, 1] }}
        transition={{ duration: onCall ? 1.3 : 2.6, repeat: Infinity, ease: easing, delay: 0.15 }}
      />
      <motion.div
        className="h-16 w-16 rounded-full bg-gradient-to-br from-accent-blue to-primary shadow-lg shadow-accent-blue/40"
        animate={{ scale: onCall ? [1, 1.1, 1] : [1, 1.04, 1] }}
        transition={{ duration: onCall ? 1 : 2, repeat: Infinity, ease: easing, delay: 0.3 }}
      />
    </div>
  );
}

export function VoiceChat() {
  // Persistent session state (survives tab switches) — lifted into ChatSessionProvider.
  const { messages, setMessages, streaming, setStreaming, onCall, setOnCall, muted, setMuted, roomRef } =
    useChatSession();

  // Local-only state (only relevant while this component is mounted).
  const [draft, setDraft] = useState("");
  const [callError, setCallError] = useState<string | null>(null);
  const [pendingMsgId, setPendingMsgId] = useState<string | null>(null);

  const { sendMessage } = useChatStream();
  const liveKit = useLiveKitCall(roomRef);

  const send = () => {
    const text = draft.trim();
    if (!text || streaming) return;
    const now = clockTime();
    const pendingId = `pending-${Date.now()}`;
    setPendingMsgId(pendingId);
    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", text, timestamp: now },
      { id: pendingId, role: "aster", text: "", timestamp: now },
    ]);
    setDraft("");
    setStreaming(true);

    void sendMessage(
      text,
      (chunk) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === pendingId ? { ...m, text: m.text + chunk } : m))
        );
      },
      () => {
        setMessages((prev) =>
          prev.map((m) => (m.id === pendingId ? { ...m, id: `a-${Date.now()}` } : m))
        );
        setPendingMsgId(null);
        setStreaming(false);
      },
      (err) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingId ? { ...m, text: `(error — is Aster running? ${err})` } : m
          )
        );
        setPendingMsgId(null);
        setStreaming(false);
      },
    );
  };

  return (
    <motion.div variants={staggerContainer} initial="initial" animate="animate">
      <motion.div variants={staggerItem}>
        <SectionHeader title="Voice & Chat" description="Talk to Aster, by typing or by voice." />
      </motion.div>

      <div className="grid grid-cols-3 gap-6">
        <motion.div variants={staggerItem} className="col-span-2">
          <Card className="flex h-[420px] flex-col">
            <div className="aster-scroll flex-1 space-y-3 overflow-y-auto pr-1">
              {messages.length === 0 && (
                <p className="text-on-surface-variant/40 text-sm italic">
                  Send a message to start chatting with Aster.
                </p>
              )}
              <AnimatePresence initial={false}>
                {messages.map((message) => (
                  <motion.div
                    key={message.id}
                    layout
                    variants={message.role === "user" ? slideFromRight : slideFromLeft}
                    initial="initial"
                    animate="animate"
                    exit="exit"
                    className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[80%] rounded-card px-3 py-2 text-body-md ${
                        message.role === "user"
                          ? "bg-accent-blue text-white"
                          : "bg-surface-elevated text-on-surface"
                      }`}
                    >
                      {message.id === pendingMsgId && message.text === "" ? (
                        <span className="inline-flex gap-1">
                          {[0, 1, 2].map((i) => (
                            <span
                              key={i}
                              className="h-1.5 w-1.5 rounded-full bg-primary/60 animate-pulse"
                              style={{ animationDelay: `${i * 0.2}s` }}
                            />
                          ))}
                        </span>
                      ) : (
                        <p>{message.text}</p>
                      )}
                      <p className="mt-1 text-[10px] opacity-60">{message.timestamp}</p>
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
            <form
              className="mt-3 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                send();
              }}
            >
              <Input
                placeholder="Message Aster…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={streaming}
              />
              <Button type="submit" disabled={!draft.trim() || streaming}>
                Send
              </Button>
            </form>
          </Card>
        </motion.div>

        <motion.div variants={staggerItem}>
          <Card className="flex flex-col items-center justify-center gap-5 py-10">
            <PresenceOrb onCall={onCall} />
            <p className="text-body-md text-on-surface-variant/70">
              {onCall ? "On a call with Aster" : "Not in a call"}
            </p>
            {callError && (
              <p className="text-xs text-error-text text-center px-2">{callError}</p>
            )}
            <div className="flex gap-3">
              <Button
                variant={onCall ? "danger" : "primary"}
                onClick={() => {
                  if (onCall) {
                    void liveKit.endCall((v) => setOnCall(v));
                  } else {
                    setCallError(null);
                    void liveKit.startCall(
                      (v) => setOnCall(v),
                      (err) => setCallError(err),
                    );
                  }
                }}
              >
                {onCall ? <PhoneOff size={18} /> : <Phone size={18} />}
                {onCall ? "End Call" : "Start Call"}
              </Button>
              <Button
                variant="outline"
                disabled={!onCall}
                onClick={() => {
                  const next = !muted;
                  setMuted(next);
                  void liveKit.setMuted(next);
                }}
              >
                {muted ? <MicOff size={18} /> : <Mic size={18} />}
                {muted ? "Unmute" : "Mute"}
              </Button>
            </div>
          </Card>
        </motion.div>
      </div>
    </motion.div>
  );
}
