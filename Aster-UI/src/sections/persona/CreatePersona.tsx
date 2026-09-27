import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { fade, scaleIn, easing } from "../../theme/motion";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { createPersona, generatePersona } from "../../api/settings";
import type { PersonaFormAnswers } from "../../types";

type Mode = "choose" | "write" | "guided" | "generating" | "success";

interface CreatePersonaProps {
  /** Called with the new persona's id once creation succeeds. */
  onCreated: (id: string, displayName: string) => void;
  /** Called when the user cancels/closes without creating anything. */
  onCancel: () => void;
}

/** Animated green checkmark drawn via framer-motion SVG pathLength. */
function CheckmarkAnim() {
  return (
    <svg viewBox="0 0 52 52" className="h-20 w-20" fill="none" strokeLinecap="round" strokeLinejoin="round">
      <motion.circle
        cx="26" cy="26" r="25"
        stroke="#22c55e"
        strokeWidth="2"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ duration: 0.5, ease: easing }}
      />
      <motion.path
        d="M14.5 27l8 8 15-15"
        stroke="#22c55e"
        strokeWidth="3"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ duration: 0.4, delay: 0.45, ease: easing }}
      />
    </svg>
  );
}

export function CreatePersona({ onCreated, onCancel }: CreatePersonaProps) {
  const [mode, setMode] = useState<Mode>("choose");

  // Write-prompt state
  const [writeName, setWriteName] = useState("");
  const [writeBody, setWriteBody] = useState("");

  // Guided form state
  const [guidedName, setGuidedName] = useState("");
  const [answers, setAnswers] = useState<PersonaFormAnswers>({
    role_description: "",
    communication_style: "",
    quirks: "",
    formality: "neutral",
    gender: "neutral",
  });

  // Success state
  const [createdName, setCreatedName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleWriteSubmit() {
    if (!writeName.trim() || !writeBody.trim()) return;
    setLoading(true);
    setError("");
    try {
      const { id } = await createPersona(writeName.trim(), writeBody.trim());
      setCreatedName(writeName.trim());
      setMode("success");
      // Auto-dismiss and return after 2.4s
      setTimeout(() => onCreated(id, writeName.trim()), 2400);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create persona.");
    } finally {
      setLoading(false);
    }
  }

  async function handleGuidedSubmit() {
    if (!guidedName.trim()) return;
    setMode("generating");
    setError("");
    try {
      const { id } = await generatePersona(guidedName.trim(), answers);
      setCreatedName(guidedName.trim());
      setMode("success");
      setTimeout(() => onCreated(id, guidedName.trim()), 2400);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Persona generation failed.");
      setMode("guided");
    }
  }

  function patchAnswers(patch: Partial<PersonaFormAnswers>) {
    setAnswers((prev) => ({ ...prev, ...patch }));
  }

  return (
    // Backdrop
    <motion.div
      key="backdrop"
      variants={fade}
      initial="initial"
      animate="animate"
      exit="exit"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      onClick={mode === "success" ? undefined : onCancel}
    >
      <motion.div
        variants={scaleIn}
        initial="initial"
        animate="animate"
        exit="exit"
        onClick={(e) => e.stopPropagation()}
        className="relative w-full max-w-md rounded-card border border-outline-custom bg-surface-card p-6"
      >
        <AnimatePresence mode="wait">

          {/* ── Mode: choose ─────────────────────────────────────── */}
          {mode === "choose" && (
            <motion.div key="choose" variants={fade} initial="initial" animate="animate" exit="exit">
              <h2 className="mb-5 font-headline-md text-headline-md text-on-surface">
                Create Persona
              </h2>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => setMode("write")}
                  className="flex flex-1 flex-col items-center gap-3 rounded-card border border-outline-custom bg-surface-elevated p-5 text-center transition-all hover:border-accent-blue/50 hover:bg-surface-elevated/80"
                >
                  <span className="text-2xl">✏️</span>
                  <span>
                    <span className="block font-label-lg text-label-lg text-on-surface">
                      Write it myself
                    </span>
                    <span className="mt-1 block text-sm text-on-surface-variant/60">
                      Paste or type a personality prompt
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => setMode("guided")}
                  className="flex flex-1 flex-col items-center gap-3 rounded-card border border-outline-custom bg-surface-elevated p-5 text-center transition-all hover:border-accent-blue/50 hover:bg-surface-elevated/80"
                >
                  <span className="text-2xl">🤖</span>
                  <span>
                    <span className="block font-label-lg text-label-lg text-on-surface">
                      Let Aster help
                    </span>
                    <span className="mt-1 block text-sm text-on-surface-variant/60">
                      Answer a few questions
                    </span>
                  </span>
                </button>
              </div>
              <div className="mt-5 flex justify-end">
                <Button variant="ghost" onClick={onCancel}>Cancel</Button>
              </div>
            </motion.div>
          )}

          {/* ── Mode: write ──────────────────────────────────────── */}
          {mode === "write" && (
            <motion.div key="write" variants={fade} initial="initial" animate="animate" exit="exit">
              <h2 className="mb-5 font-headline-md text-headline-md text-on-surface">
                Write the prompt
              </h2>

              <label className="mb-1 block text-xs text-on-surface-variant/70">
                Persona name
              </label>
              <Input
                className="mb-4"
                placeholder="e.g. Coach"
                value={writeName}
                onChange={(e) => setWriteName(e.target.value)}
              />

              <label className="mb-1 block text-xs text-on-surface-variant/70">
                Personality prompt{" "}
                <span className="text-on-surface-variant/40">(personality only — tool rules are added automatically)</span>
              </label>
              <textarea
                className="mb-1 min-h-[160px] w-full rounded-input border-none bg-surface-elevated px-4 py-3 text-sm text-on-surface outline-none transition-all placeholder:text-on-surface-variant/40 focus:ring-2 focus:ring-accent-blue/50 resize-y"
                placeholder="You are a no-nonsense personal coach who pushes Mohamed hard…"
                value={writeBody}
                onChange={(e) => setWriteBody(e.target.value)}
              />

              {error && <p className="mb-2 text-sm text-error-text">{error}</p>}

              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setMode("choose")}>Back</Button>
                <Button
                  onClick={handleWriteSubmit}
                  disabled={!writeName.trim() || !writeBody.trim() || loading}
                >
                  {loading ? "Saving…" : "Create Persona"}
                </Button>
              </div>
            </motion.div>
          )}

          {/* ── Mode: guided ─────────────────────────────────────── */}
          {mode === "guided" && (
            <motion.div key="guided" variants={fade} initial="initial" animate="animate" exit="exit">
              <h2 className="mb-5 font-headline-md text-headline-md text-on-surface">
                Let Aster Help You
              </h2>

              <label className="mb-1 block text-xs text-on-surface-variant/70">
                What should I call this persona?
              </label>
              <Input
                className="mb-4"
                placeholder="e.g. Gogi"
                value={guidedName}
                onChange={(e) => setGuidedName(e.target.value)}
              />

              <label className="mb-1 block text-xs text-on-surface-variant/70">
                How would a friend describe them?
              </label>
              <Input
                className="mb-4"
                placeholder="e.g. chill, funny, hypes me up"
                value={answers.role_description}
                onChange={(e) => patchAnswers({ role_description: e.target.value })}
              />

              <label className="mb-1 block text-xs text-on-surface-variant/70">
                How do they talk to you?
              </label>
              <Input
                className="mb-4"
                placeholder="e.g. casual, short sentences, direct"
                value={answers.communication_style}
                onChange={(e) => patchAnswers({ communication_style: e.target.value })}
              />

              <label className="mb-1 block text-xs text-on-surface-variant/70">
                Any quirks or catchphrases?
              </label>
              <Input
                className="mb-4"
                placeholder="e.g. always says 'let's go', no emojis ever"
                value={answers.quirks}
                onChange={(e) => patchAnswers({ quirks: e.target.value })}
              />

              <label className="mb-1 block text-xs text-on-surface-variant/70">
                Gender — what gender is this persona?
              </label>
              <div className="mb-4 flex gap-3">
                {(["male", "female", "neutral"] as const).map((g) => (
                  <button
                    key={g}
                    type="button"
                    onClick={() => patchAnswers({ gender: g })}
                    className={`flex-1 rounded-input border py-2 text-sm font-medium capitalize transition-all ${
                      answers.gender === g
                        ? "border-accent-blue bg-accent-blue/15 text-primary"
                        : "border-outline-custom bg-surface-elevated text-on-surface-variant hover:border-accent-blue/40"
                    }`}
                  >
                    {g}
                  </button>
                ))}
              </div>

              <label className="mb-1 block text-xs text-on-surface-variant/70">
                Formality
              </label>
              <div className="mb-5 flex gap-3">
                {(["butler", "buddy", "neutral"] as const).map((f) => (
                  <button
                    key={f}
                    type="button"
                    onClick={() => patchAnswers({ formality: f })}
                    className={`flex-1 rounded-input border py-2 text-sm font-medium capitalize transition-all ${
                      answers.formality === f
                        ? "border-accent-blue bg-accent-blue/15 text-primary"
                        : "border-outline-custom bg-surface-elevated text-on-surface-variant hover:border-accent-blue/40"
                    }`}
                  >
                    {f}
                  </button>
                ))}
              </div>

              {error && <p className="mb-2 text-sm text-error-text">{error}</p>}

              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setMode("choose")}>Back</Button>
                <Button
                  onClick={handleGuidedSubmit}
                  disabled={!guidedName.trim()}
                >
                  Build my persona
                </Button>
              </div>
            </motion.div>
          )}

          {/* ── Mode: generating ─────────────────────────────────── */}
          {mode === "generating" && (
            <motion.div
              key="generating"
              variants={fade}
              initial="initial"
              animate="animate"
              exit="exit"
              className="flex flex-col items-center py-10 text-center"
            >
              <div className="mb-4 h-8 w-8 rounded-full border-2 border-accent-blue border-t-transparent animate-spin" />
              <p className="text-body-md text-on-surface">
                Aster is building your persona…
              </p>
              <p className="mt-1 text-sm text-on-surface-variant/60">This may take a few seconds.</p>
            </motion.div>
          )}

          {/* ── Mode: success ─────────────────────────────────────── */}
          {mode === "success" && (
            <motion.div
              key="success"
              variants={fade}
              initial="initial"
              animate="animate"
              exit="exit"
              className="flex flex-col items-center py-10 text-center"
            >
              <CheckmarkAnim />
              <p className="mt-5 font-headline-md text-headline-md text-on-surface">
                {createdName} personality has been created
              </p>
            </motion.div>
          )}

        </AnimatePresence>
      </motion.div>
    </motion.div>
  );
}
