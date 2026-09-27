import { useState, useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Play, Loader2, UserCircle2, Wand2 } from "lucide-react";
import { fadeSlideUp } from "../theme/motion";
import { Select } from "../components/ui/Select";
import { Button } from "../components/ui/Button";
import { CreatePersona } from "../sections/persona/CreatePersona";
import {
  fetchPersonas,
  fetchVoices,
  selectPersona,
  selectVoice,
  previewVoice,
  fetchPresets,
  selectPreset,
} from "../api/settings";
import type { Persona, KokoroVoice, Preset } from "../types";

interface PersonaStepProps {
  onContinue: () => void;
}

export function PersonaStep({ onContinue }: PersonaStepProps) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [activePersona, setActivePersona] = useState("jarvis");
  const [personaLoading, setPersonaLoading] = useState(false);

  const [voices, setVoices] = useState<KokoroVoice[]>([]);
  const [activeVoice, setActiveVoice] = useState("af_bella");
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetApplying, setPresetApplying] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);

  useEffect(() => {
    fetchPersonas()
      .then(({ active, personas }) => {
        setActivePersona(active);
        setPersonas(personas);
      })
      .catch(() => {
        // Backend not running during onboarding — show defaults
        setPersonas([
          { id: "jarvis", name: "Jarvis", builtin: true },
          { id: "gogi", name: "Gogi", builtin: true },
        ]);
      });

    fetchVoices()
      .then(({ active, voices: vs }) => {
        setActiveVoice(active ?? "af_bella");
        setVoices(vs ?? []);
      })
      .catch(() => {});

    fetchPresets()
      .then(({ presets: ps }) => setPresets(ps ?? []))
      .catch(() => {
        // Backend offline — show built-in defaults
        setPresets([
          { id: "jarvis_default", name: "Jarvis — British Butler", persona: "jarvis", voice: "bm_george", builtin: true },
          { id: "gogi_default",   name: "Gogi — Casual Friend",   persona: "gogi",   voice: "am_michael", builtin: true },
        ]);
      });
  }, []);

  async function handleApplyPreset(id: string) {
    if (presetApplying) return;
    setPresetApplying(id);
    try {
      const { persona, voice } = await selectPreset(id);
      setActivePersona(persona);
      setActiveVoice(voice);
    } catch {
      // Offline — set state from local preset data
      const found = presets.find((p) => p.id === id);
      if (found) {
        setActivePersona(found.persona);
        setActiveVoice(found.voice);
      }
    } finally {
      setPresetApplying(null);
    }
  }

  async function handlePersonaSelect(id: string) {
    setPersonaLoading(true);
    try {
      await selectPersona(id);
      setActivePersona(id);
    } catch {
      setActivePersona(id); // still advance local state
    } finally {
      setPersonaLoading(false);
    }
  }

  async function handleVoiceSelect(v: string) {
    setVoiceLoading(true);
    try {
      await selectVoice(v);
      setActiveVoice(v);
    } catch {
      setActiveVoice(v);
    } finally {
      setVoiceLoading(false);
    }
  }

  async function handlePreview() {
    if (previewing) return;
    setPreviewing(true);
    try {
      const url = await previewVoice(activeVoice);
      const audio = new Audio(url);
      audio.onended = () => {
        setPreviewing(false);
        URL.revokeObjectURL(url);
      };
      audio.onerror = () => {
        setPreviewing(false);
        URL.revokeObjectURL(url);
      };
      await audio.play();
    } catch {
      setPreviewing(false);
    }
  }

  function handlePersonaCreated(id: string, displayName: string) {
    setShowCreate(false);
    setPersonas((prev) =>
      prev.some((p) => p.id === id)
        ? prev
        : [...prev, { id, name: displayName, builtin: false }],
    );
    setActivePersona(id);
    selectPersona(id).catch(() => {});
  }

  return (
    <>
      <motion.div
        variants={fadeSlideUp}
        initial="initial"
        animate="animate"
        exit="exit"
        className="flex w-full max-w-sm flex-col items-center text-center"
      >
        <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-accent-blue/15">
          <UserCircle2 size={32} className="text-primary" />
        </div>

        <h1 className="font-headline-lg text-headline-lg text-white/90">Choose a persona</h1>
        <p className="mt-2 text-body-md text-on-surface-variant/70">
          Pick how Aster talks to you, and a voice to match.
        </p>

        <div className="mt-8 w-full text-left">
          {/* ── Preset quick-pick ──────────────────────────────────── */}
          {presets.length > 0 && (
            <>
              <label className="mb-1 block text-xs text-on-surface-variant/70">Quick-start presets</label>
              <div className="mb-5 flex flex-col gap-1.5">
                {presets.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => handleApplyPreset(preset.id)}
                    disabled={!!presetApplying}
                    className={`flex items-center justify-between rounded-input border px-3 py-2 text-sm transition-all disabled:opacity-50 ${
                      activePersona === preset.persona && activeVoice === preset.voice
                        ? "border-accent-blue bg-accent-blue/10 text-primary"
                        : "border-outline-custom bg-surface-elevated text-on-surface hover:border-accent-blue/40"
                    }`}
                  >
                    <span>{preset.name}</span>
                    {presetApplying === preset.id && (
                      <Loader2 size={12} className="animate-spin" />
                    )}
                  </button>
                ))}
              </div>
            </>
          )}

          {/* ── Persona picker ─────────────────────────────────────── */}
          <label className="mb-1 block text-xs text-on-surface-variant/70">Active persona</label>
          <div className="mb-2 flex gap-2">
            <div className="flex-1">
              <Select
                value={activePersona}
                onChange={(e) => handlePersonaSelect(e.target.value)}
                disabled={personaLoading}
              >
                {personas.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}{p.builtin ? "" : " (custom)"}
                  </option>
                ))}
              </Select>
            </div>
            {personaLoading && (
              <div className="flex items-center px-2">
                <Loader2 size={16} className="animate-spin text-accent-blue" />
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="mb-6 flex items-center gap-1.5 text-sm text-accent-blue hover:underline"
          >
            <Wand2 size={13} />
            Create a new persona
          </button>

          {/* ── Voice picker ───────────────────────────────────────── */}
          <label className="mb-1 block text-xs text-on-surface-variant/70">Voice</label>
          <div className="flex gap-2">
            <div className="flex-1">
              <Select
                value={activeVoice}
                onChange={(e) => handleVoiceSelect(e.target.value)}
                disabled={voiceLoading || voices.length === 0}
              >
                {voices.length === 0 && <option value="af_bella">Bella — American female</option>}
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.custom
                      ? `${v.label} (custom)`
                      : `${v.label} — ${v.accent ?? ""} ${v.gender ?? ""}`.trim()}
                  </option>
                ))}
              </Select>
            </div>
            <Button
              variant="outline"
              onClick={handlePreview}
              disabled={previewing || voices.length === 0}
              className="shrink-0 gap-1.5"
            >
              {previewing ? (
                <><Loader2 size={14} className="animate-spin" /> …</>
              ) : (
                <><Play size={14} /> Preview</>
              )}
            </Button>
          </div>
        </div>

        <Button className="mt-8 w-full" onClick={onContinue}>
          Continue
        </Button>
      </motion.div>

      {/* ── Create Persona overlay (portal-like, rendered outside the step) ── */}
      <AnimatePresence>
        {showCreate && (
          <CreatePersona
            key="onboarding-create-persona"
            onCreated={handlePersonaCreated}
            onCancel={() => setShowCreate(false)}
          />
        )}
      </AnimatePresence>
    </>
  );
}
