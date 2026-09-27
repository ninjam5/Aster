import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Eye, Mail, Send, ShieldCheck } from "lucide-react";
import { Card } from "../components/ui/Card";
import { Chip } from "../components/ui/Chip";
import { Button } from "../components/ui/Button";
import { fetchGoogleStatus, connectGoogleAccount, setGoogleTier } from "../api/google";
import { cardHover } from "../theme/motion";
import type { GoogleAccessTier, GoogleStatus } from "../types";

const TIER_OPTIONS: {
  id: GoogleAccessTier;
  label: string;
  description: string;
  icon: typeof Eye;
}[] = [
  {
    id: "limited",
    label: "Limited Access",
    description: "Read-only — Aster can see your email and calendar but never changes anything.",
    icon: Eye,
  },
  {
    id: "partial",
    label: "Partial Access",
    description: "Aster can modify mail/events and draft replies, but always shows you the draft before sending.",
    icon: ShieldCheck,
  },
  {
    id: "autonomous",
    label: "Autonomous Access",
    description: "Aster replies and creates invites immediately, with a built-in safety guardrail on new/unusual sends.",
    icon: Send,
  },
];

export function GoogleCard() {
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [updatingTier, setUpdatingTier] = useState<GoogleAccessTier | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    fetchGoogleStatus()
      .then(setStatus)
      .catch(() => {/* face_server offline — leave status as-is */});
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  const handleConnect = async () => {
    setError(null);
    setConnecting(true);
    try {
      await connectGoogleAccount();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to connect.");
    } finally {
      setConnecting(false);
    }
  };

  const handleTierSelect = async (tier: GoogleAccessTier) => {
    if (!status || tier === status.tier) return;
    setError(null);
    setUpdatingTier(tier);
    try {
      await setGoogleTier(tier);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update access level.");
    } finally {
      setUpdatingTier(null);
    }
  };

  const statusLabel = !status?.configured
    ? "Not configured"
    : status.needs_reconnect
      ? "Needs reconnect"
      : status.connected
        ? "Connected"
        : "Not connected";
  const statusTone = !status?.configured
    ? "neutral"
    : status.needs_reconnect
      ? "warning"
      : status.connected
        ? "success"
        : "neutral";

  return (
    <motion.div whileHover={cardHover}>
      <Card className={status?.connected && !status.needs_reconnect ? "border-accent-blue/40" : ""}>
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent-blue/15">
              <Mail size={20} className="text-accent-blue" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-label-lg text-label-lg text-on-surface">Gmail + Calendar</h3>
                <Chip tone={statusTone as "neutral" | "success" | "warning"}>{statusLabel}</Chip>
              </div>
              <p className="mt-1 text-body-md text-on-surface-variant/70">
                Read, summarize, and (at higher access levels) act on your email and calendar.
              </p>
              {status?.connected && !status.needs_reconnect && status.unread_count > 0 && (
                <p className="mt-1 text-xs text-on-surface-variant/50">
                  {status.unread_count} unread in your primary inbox
                </p>
              )}
            </div>
          </div>
        </div>

        {!status?.configured && (
          <p className="mt-4 text-xs text-on-surface-variant/50">
            Set <code>google.client_id</code> / <code>google.client_secret</code> in{" "}
            <code>secrets.yaml</code> to enable this integration.
          </p>
        )}

        {status?.configured && status.reauth_needed && (
          <div className="mt-4 flex items-center gap-2 rounded-input bg-amber-500/15 px-3 py-2 text-xs text-amber-300">
            <AlertTriangle size={14} />
            Google access has lapsed and needs reauthorization (expected roughly every 7 days).
          </div>
        )}

        {status?.configured && (!status.connected || status.needs_reconnect) && (
          <div className="mt-4">
            <Button variant="primary" onClick={handleConnect} disabled={connecting}>
              {connecting ? "Waiting for browser authorization..." : status.needs_reconnect ? "Reconnect Google Account" : "Connect Google Account"}
            </Button>
          </div>
        )}

        {status?.configured && status.connected && !status.needs_reconnect && (
          <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {TIER_OPTIONS.map((option) => {
              const active = status.tier === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => handleTierSelect(option.id)}
                  disabled={updatingTier !== null}
                  className={`group flex flex-col items-start gap-2 rounded-card border p-3 text-left transition-all disabled:cursor-not-allowed ${
                    active
                      ? "border-accent-blue bg-accent-blue/10"
                      : "border-outline-custom bg-surface-card hover:border-accent-blue/50 hover:bg-surface-elevated"
                  }`}
                >
                  <div className="flex w-full items-center justify-between">
                    <option.icon size={18} className={active ? "text-accent-blue" : "text-on-surface-variant/70"} />
                    {active && <CheckCircle2 size={14} className="text-accent-blue" />}
                  </div>
                  <span className="font-label-lg text-label-lg text-on-surface">
                    {updatingTier === option.id ? "Updating..." : option.label}
                  </span>
                  <span className="text-xs text-on-surface-variant/60">{option.description}</span>
                </button>
              );
            })}
          </div>
        )}

        {error && <p className="mt-3 text-xs text-error-text">{error}</p>}
      </Card>
    </motion.div>
  );
}
