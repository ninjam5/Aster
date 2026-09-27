import { useState, useEffect, useCallback, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Play, Loader2, RotateCcw, X } from "lucide-react";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { Select } from "../components/ui/Select";
import { Button } from "../components/ui/Button";
import { fade, scaleIn, staggerContainer, staggerItem } from "../theme/motion";
import { CreatePersona } from "./persona/CreatePersona";
import { useCharacterStyle, type CharacterStyle } from "../character/useCharacterStyle";
import {
  fetchPersonas,
  selectPersona,
  fetchVoices,
  selectVoice,
  previewVoice,
  fetchLlmSettings,
  updateLlmSettings,
  fetchPresets,
  createPreset,
  selectPreset,
  deletePreset,
} from "../api/settings";
import type { UserProfile, TechLevel, Persona, KokoroVoice, LlmSettings, Preset } from "../types";

interface SettingsProps {
  profile: UserProfile;
  onUpdateName: (name: string) => void;
  onUpdateTechLevel: (techLevel: TechLevel) => void;
  onUpdateModel: (modelId: string) => void;
  onResetOnboarding: () => void;
}

export function Settings({
  profile,
  onUpdateName,
  onUpdateTechLevel,
  onResetOnboarding,
}: SettingsProps) {
  const [name, setName] = useState(profile.name);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [characterStyle, setCharacterStyle] = useCharacterStyle();

  // ── Preset state ───────────────────────────────────────────────────────────
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetSaving, setPresetSaving] = useState(false);
  const [presetApplying, setPresetApplying] = useState<string | null>(null);
  const [newPresetName, setNewPresetName] = useState("");

  // ── Persona state ──────────────────────────────────────────────────────────
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [activePersona, setActivePersona] = useState<string>("");
  const [personaLoading, setPersonaLoading] = useState(false);
  const [showCreatePersona, setShowCreatePersona] = useState(false);

  // ── Voice state ────────────────────────────────────────────────────────────
  const [voices, setVoices] = useState<KokoroVoice[]>([]);
  const [activeVoice, setActiveVoice] = useState<string>("");
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const previewUrlRef = useRef<string | null>(null);

  // ── LLM settings state ─────────────────────────────────────────────────────
  const [llmDraft, setLlmDraft] = useState<LlmSettings | null>(null);
  const [llmSaving, setLlmSaving] = useState(false);
  const [restartBanner, setRestartBanner] = useState(false);

  // ── Load data ─────────────────────────────────────────────────────────────
  useEffect(() => {
    fetchPersonas()
      .then(({ active, personas: ps }) => {
        setActivePersona(active ?? "");
        setPersonas(ps ?? []);
      })
      .catch(() => {});

    fetchVoices()
      .then(({ active, voices: vs }) => {
        setActiveVoice(active ?? "");
        setVoices(vs ?? []);
      })
      .catch(() => {});

    fetchPresets()
      .then(({ presets: ps }) => setPresets(ps ?? []))
      .catch(() => {
        // Offline fallback — show the two built-in defaults
        setPresets([
          { id: "jarvis_default", name: "Jarvis — British Butler", persona: "jarvis", voice: "bm_george", builtin: true },
          { id: "gogi_default",   name: "Gogi — Casual Friend",   persona: "gogi",   voice: "am_michael", builtin: true },
        ]);
      });

    if (profile.techLevel === "advanced") {
      fetchLlmSettings()
        .then((s) => {
          setLlmDraft(s);
        })
        .catch(() => {});
    }
  }, [profile.techLevel]);

  // ── Persona handlers ───────────────────────────────────────────────────────
  async function handleSelectPersona(id: string) {
    if (id === activePersona) return;
    setPersonaLoading(true);
    try {
      await selectPersona(id);
      setActivePersona(id);
    } catch {
      // silently fail — persona doesn't reload without the backend running
    } finally {
      setPersonaLoading(false);
    }
  }

  function handlePersonaCreated(id: string, displayName: string) {
    setShowCreatePersona(false);
    // Optimistically add + select
    setPersonas((prev) =>
      prev.some((p) => p.id === id)
        ? prev
        : [...prev, { id, name: displayName, builtin: false }],
    );
    setActivePersona(id);
    selectPersona(id).catch(() => {});
  }

  // ── Preset handlers ────────────────────────────────────────────────────────

  async function handleApplyPreset(id: string) {
    if (presetApplying) return;
    setPresetApplying(id);
    try {
      const { persona, voice } = await selectPreset(id);
      setActivePersona(persona);
      setActiveVoice(voice);
    } catch {
      // ignore when offline
    } finally {
      setPresetApplying(null);
    }
  }

  async function handleSavePreset() {
    const name = newPresetName.trim();
    if (!name || !activePersona || !activeVoice) return;
    setPresetSaving(true);
    try {
      const { id } = await createPreset(name, activePersona, activeVoice);
      setPresets((prev) =>
        prev.some((p) => p.id === id)
          ? prev.map((p) => p.id === id ? { ...p, name } : p)
          : [...prev, { id, name, persona: activePersona, voice: activeVoice, builtin: false }],
      );
      setNewPresetName("");
    } catch {
      // ignore
    } finally {
      setPresetSaving(false);
    }
  }

  async function handleDeletePreset(id: string) {
    try {
      await deletePreset(id);
      setPresets((prev) => prev.filter((p) => p.id !== id));
    } catch {
      // ignore
    }
  }

  // ── Voice handlers ─────────────────────────────────────────────────────────
  async function handleSelectVoice(v: string) {
    if (v === activeVoice) return;
    setVoiceLoading(true);
    try {
      await selectVoice(v);
      setActiveVoice(v);
    } catch {
      // ignore when backend is offline
    } finally {
      setVoiceLoading(false);
    }
  }

  async function handlePreview() {
    if (previewing) {
      audioRef.current?.pause();
      setPreviewing(false);
      return;
    }
    setPreviewing(true);
    try {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      const url = await previewVoice(activeVoice);
      previewUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => setPreviewing(false);
      audio.onerror = () => setPreviewing(false);
      await audio.play();
    } catch {
      setPreviewing(false);
    }
  }

  // ── LLM settings handlers ──────────────────────────────────────────────────
  const saveLlmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const debouncedSaveLlm = useCallback((draft: LlmSettings) => {
    if (saveLlmTimer.current) clearTimeout(saveLlmTimer.current);
    saveLlmTimer.current = setTimeout(async () => {
      setLlmSaving(true);
      try {
        const { restart_required } = await updateLlmSettings({
          temperature: draft.temperature,
          top_p: draft.top_p,
          top_k: draft.top_k,
        });
        if (restart_required) setRestartBanner(true);
      } catch {
        // ignore
      } finally {
        setLlmSaving(false);
      }
    }, 600);
  }, []);

  async function saveRestartKnobs() {
    if (!llmDraft) return;
    setLlmSaving(true);
    try {
      const { restart_required } = await updateLlmSettings({
        context_window: llmDraft.context_window,
        kv_cache_type: llmDraft.kv_cache_type,
      });
      if (restart_required) setRestartBanner(true);
    } catch {
      // ignore
    } finally {
      setLlmSaving(false);
    }
  }

  function patchLlm(patch: Partial<LlmSettings>) {
    setLlmDraft((prev) => {
      if (!prev) return prev;
      const next = { ...prev, ...patch };
      debouncedSaveLlm(next);
      return next;
    });
  }

  return (
    <motion.div variants={staggerContainer} initial="initial" animate="animate">
      <motion.div variants={staggerItem}>
        <SectionHeader title="Settings" description="Manage your profile and preferences." />
      </motion.div>

      {/* ── Profile ─────────────────────────────────────────────────────────── */}
      <motion.div variants={staggerItem}>
        <Card className="mb-4">
          <h2 className="mb-5 font-label-lg text-label-lg text-on-surface">Profile</h2>

          <label className="mb-1 block text-xs text-on-surface-variant/70">Name</label>
          <div className="mb-5 flex gap-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
            <Button onClick={() => onUpdateName(name)} disabled={!name.trim()}>
              Save
            </Button>
          </div>

          <label className="mb-1 block text-xs text-on-surface-variant/70">Technical level</label>
          <Select
            value={profile.techLevel}
            onChange={(e) => onUpdateTechLevel(e.target.value as TechLevel)}
          >
            <option value="basic">Basic User</option>
            <option value="advanced">Advanced User</option>
          </Select>
        </Card>
      </motion.div>

      {/* ── Appearance ──────────────────────────────────────────────────────── */}
      <motion.div variants={staggerItem}>
        <Card className="mb-4">
          <h2 className="mb-1 font-label-lg text-label-lg text-on-surface">Appearance</h2>
          <p className="mb-4 text-body-md text-on-surface-variant/60">
            How Aster appears around the app.
          </p>

          <label className="mb-1 block text-xs text-on-surface-variant/70">Character style</label>
          <Select
            value={characterStyle}
            onChange={(e) => setCharacterStyle(e.target.value as CharacterStyle)}
            aria-label="Character style"
          >
            <option value="pixel">Pixel — 8-bit sprite with animated transitions</option>
            <option value="cartoon">Cartoon — original illustrated puppet</option>
          </Select>
        </Card>
      </motion.div>

      {/* ── Presets ─────────────────────────────────────────────────────────── */}
      <motion.div variants={staggerItem}>
        <Card className="mb-4">
          <h2 className="mb-1 font-label-lg text-label-lg text-on-surface">Presets</h2>
          <p className="mb-4 text-body-md text-on-surface-variant/60">
            Saved persona + voice combos. Click to apply both at once.
          </p>

          <div className="mb-4 flex flex-col gap-2">
            {presets.map((preset) => (
              <div
                key={preset.id}
                className="flex items-center gap-2 rounded-input border border-outline-custom bg-surface-elevated px-3 py-2"
              >
                <button
                  type="button"
                  onClick={() => handleApplyPreset(preset.id)}
                  disabled={!!presetApplying}
                  className="flex-1 text-left text-sm text-on-surface hover:text-primary disabled:opacity-50"
                >
                  {presetApplying === preset.id ? (
                    <span className="flex items-center gap-1.5">
                      <Loader2 size={12} className="animate-spin" /> {preset.name}
                    </span>
                  ) : preset.name}
                </button>
                <span className="text-xs text-on-surface-variant/40">
                  {preset.persona} · {preset.voice}
                </span>
                {!preset.builtin && (
                  <button
                    type="button"
                    aria-label={`Delete preset: ${preset.name}`}
                    onClick={() => handleDeletePreset(preset.id)}
                    className="ml-1 text-on-surface-variant/40 hover:text-error-text"
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            ))}
          </div>

          <div className="flex gap-2">
            <Input
              placeholder="Name this preset…"
              value={newPresetName}
              onChange={(e) => setNewPresetName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void handleSavePreset(); }}
            />
            <Button
              variant="outline"
              onClick={handleSavePreset}
              disabled={!newPresetName.trim() || !activePersona || !activeVoice || presetSaving}
              className="shrink-0"
            >
              {presetSaving ? <Loader2 size={14} className="animate-spin" /> : "Save current"}
            </Button>
          </div>
        </Card>
      </motion.div>

      {/* ── Persona ─────────────────────────────────────────────────────────── */}
      <motion.div variants={staggerItem}>
        <Card className="mb-4">
          <h2 className="mb-1 font-label-lg text-label-lg text-on-surface">Persona</h2>
          <p className="mb-5 text-body-md text-on-surface-variant/60">
            Choose how Aster speaks and behaves.
          </p>

          <label className="mb-1 block text-xs text-on-surface-variant/70">Active persona</label>
          <div className="mb-4 flex gap-2">
            <div className="flex-1">
              <Select
                value={activePersona}
                onChange={(e) => handleSelectPersona(e.target.value)}
                disabled={personaLoading || personas.length === 0}
              >
                {personas.length === 0 && (
                  <option value="">Loading…</option>
                )}
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

          <Button variant="outline" onClick={() => setShowCreatePersona(true)}>
            + Create persona
          </Button>
        </Card>
      </motion.div>

      {/* ── Voice ───────────────────────────────────────────────────────────── */}
      <motion.div variants={staggerItem}>
        <Card className="mb-4">
          <h2 className="mb-1 font-label-lg text-label-lg text-on-surface">Voice</h2>
          <p className="mb-5 text-body-md text-on-surface-variant/60">
            Choose Aster's speaking voice.
          </p>

          <label className="mb-1 block text-xs text-on-surface-variant/70">TTS voice</label>
          <div className="flex gap-2">
            <div className="flex-1">
              <Select
                value={activeVoice}
                onChange={(e) => handleSelectVoice(e.target.value)}
                disabled={voiceLoading || voices.length === 0}
              >
                {voices.length === 0 && <option value="">Loading…</option>}
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
              disabled={!activeVoice || voices.length === 0}
              className="shrink-0 gap-1.5"
            >
              {previewing ? (
                <><Loader2 size={14} className="animate-spin" /> Stop</>
              ) : (
                <><Play size={14} /> Preview</>
              )}
            </Button>
          </div>
        </Card>
      </motion.div>

      {/* ── Advanced LLM (advanced users only) ──────────────────────────────── */}
      {profile.techLevel === "advanced" && llmDraft && (
        <motion.div variants={staggerItem}>
          <Card className="mb-4">
            <div className="mb-1 flex items-center justify-between">
              <h2 className="font-label-lg text-label-lg text-on-surface">LLM Settings</h2>
              {llmSaving && <Loader2 size={14} className="animate-spin text-accent-blue" />}
            </div>
            <p className="mb-5 text-body-md text-on-surface-variant/60">
              Sampling knobs apply immediately. Context &amp; KV-cache require restarting llama-server.
            </p>

            <p className="mb-3 text-xs font-medium uppercase tracking-wider text-on-surface-variant/50">
              Live — applies now
            </p>

            <div className="mb-5 grid grid-cols-3 gap-3">
              <div>
                <label className="mb-1 block text-xs text-on-surface-variant/70">Temperature</label>
                <input
                  type="number"
                  min={0} max={2} step={0.05}
                  className="h-touch_target w-full rounded-input border-none bg-surface-elevated px-3 text-sm text-on-surface outline-none focus:ring-2 focus:ring-accent-blue/50"
                  value={llmDraft.temperature}
                  onChange={(e) => patchLlm({ temperature: parseFloat(e.target.value) || 0 })}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-on-surface-variant/70">Top-P</label>
                <input
                  type="number"
                  min={0} max={1} step={0.01}
                  className="h-touch_target w-full rounded-input border-none bg-surface-elevated px-3 text-sm text-on-surface outline-none focus:ring-2 focus:ring-accent-blue/50"
                  value={llmDraft.top_p}
                  onChange={(e) => patchLlm({ top_p: parseFloat(e.target.value) || 0 })}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-on-surface-variant/70">Top-K</label>
                <input
                  type="number"
                  min={1} max={200} step={1}
                  className="h-touch_target w-full rounded-input border-none bg-surface-elevated px-3 text-sm text-on-surface outline-none focus:ring-2 focus:ring-accent-blue/50"
                  value={llmDraft.top_k}
                  onChange={(e) => patchLlm({ top_k: parseInt(e.target.value) || 1 })}
                />
              </div>
            </div>

            <div className="mb-3 flex items-center gap-2">
              <p className="text-xs font-medium uppercase tracking-wider text-on-surface-variant/50">
                Restart required
              </p>
              <span className="rounded bg-surface-elevated px-1.5 py-0.5 text-xs text-on-surface-variant/60">
                ⚠ changes take effect after restarting llama-server (start.bat)
              </span>
            </div>

            <div className="mb-4 grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs text-on-surface-variant/70">Context window (tokens)</label>
                <input
                  type="number"
                  min={4096} max={256000} step={1024}
                  className="h-touch_target w-full rounded-input border-none bg-surface-elevated px-3 text-sm text-on-surface outline-none focus:ring-2 focus:ring-accent-blue/50"
                  value={llmDraft.context_window}
                  onChange={(e) =>
                    setLlmDraft((prev) => prev ? { ...prev, context_window: parseInt(e.target.value) || 4096 } : prev)
                  }
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-on-surface-variant/70">KV-cache type</label>
                <Select
                  value={llmDraft.kv_cache_type}
                  onChange={(e) =>
                    setLlmDraft((prev) => prev ? { ...prev, kv_cache_type: e.target.value } : prev)
                  }
                >
                  <option value="f16">f16 (full precision)</option>
                  <option value="q8_0">q8_0</option>
                  <option value="q4_0">q4_0 (default, saves VRAM)</option>
                  <option value="q4_1">q4_1</option>
                </Select>
              </div>
            </div>

            {restartBanner && (
              <div className="mb-3 flex items-center gap-2 rounded-input border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
                <RotateCcw size={14} />
                Restart llama-server (start.bat) to apply context / KV-cache changes.
              </div>
            )}

            <Button variant="outline" onClick={saveRestartKnobs} disabled={llmSaving}>
              Save restart-gated settings
            </Button>
          </Card>
        </motion.div>
      )}

      {/* ── Danger zone ──────────────────────────────────────────────────────── */}
      <motion.div variants={staggerItem}>
        <Card>
          <h2 className="mb-2 font-label-lg text-label-lg text-on-surface">Danger zone</h2>
          <p className="mb-4 text-body-md text-on-surface-variant/70">
            This clears your saved profile and shows the welcome screen again.
          </p>
          <Button variant="danger" onClick={() => setConfirmOpen(true)}>
            Reset onboarding
          </Button>
        </Card>
      </motion.div>

      {/* ── Reset confirm dialog ─────────────────────────────────────────────── */}
      <AnimatePresence>
        {confirmOpen && (
          <motion.div
            key="backdrop"
            variants={fade}
            initial="initial"
            animate="animate"
            exit="exit"
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
            onClick={() => setConfirmOpen(false)}
          >
            <motion.div
              variants={scaleIn}
              initial="initial"
              animate="animate"
              exit="exit"
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-sm rounded-card border border-outline-custom bg-surface-card p-6"
            >
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-error-container text-error-text">
                  <AlertTriangle size={20} />
                </div>
                <h3 className="font-headline-md text-headline-md text-on-surface">Reset onboarding?</h3>
              </div>
              <p className="mt-3 text-body-md text-on-surface-variant/70">
                This clears your saved profile (name, technical level, model) and takes you back
                to the welcome screen. This can't be undone.
              </p>
              <div className="mt-6 flex justify-end gap-2">
                <Button variant="outline" onClick={() => setConfirmOpen(false)}>
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  onClick={() => {
                    setConfirmOpen(false);
                    onResetOnboarding();
                  }}
                >
                  Yes, reset onboarding
                </Button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Create Persona overlay ───────────────────────────────────────────── */}
      <AnimatePresence>
        {showCreatePersona && (
          <CreatePersona
            key="create-persona"
            onCreated={handlePersonaCreated}
            onCancel={() => setShowCreatePersona(false)}
          />
        )}
      </AnimatePresence>
    </motion.div>
  );
}
