import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { Variants } from "framer-motion";
import { Brain, MessageCircle, MessageSquare, PenLine, Search, Trash2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { asterGet, asterPost } from "../api/client";
import { easing, fade, staggerContainer, staggerItem } from "../theme/motion";
import type { MemoryFact } from "../types";

// Per-source visual identity so the list is scannable at a glance.
const SOURCE_STYLE: Record<MemoryFact["source"], { icon: LucideIcon; classes: string }> = {
  conversation: { icon: MessageCircle, classes: "bg-accent-blue/15 text-primary" },
  discord: { icon: MessageSquare, classes: "bg-social-discord/15 text-social-discord" },
  manual: { icon: PenLine, classes: "bg-emerald-500/15 text-emerald-300" },
};

const rowVariants: Variants = {
  initial: { opacity: 0, height: 0 },
  animate: { opacity: 1, height: "auto", transition: { duration: 0.25, ease: easing } },
  exit: { opacity: 0, height: 0, transition: { duration: 0.2, ease: easing } },
};

export function Memory() {
  const [query, setQuery] = useState("");
  const [newFact, setNewFact] = useState("");
  const [memories, setMemories] = useState<MemoryFact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const delay = query ? 300 : 0;
    const id = setTimeout(() => {
      const url = query ? `/api/memory?q=${encodeURIComponent(query)}` : "/api/memory";
      asterGet<MemoryFact[]>(url)
        .then((data) => { setMemories(data); setError(null); })
        .catch(() => setError("Could not reach Aster. Is it running?"))
        .finally(() => setLoading(false));
    }, delay);
    return () => clearTimeout(id);
  }, [query]);

  const addFact = async () => {
    const text = newFact.trim();
    if (!text) return;
    const optimistic: MemoryFact = {
      id: `local-${Date.now()}`,
      text,
      savedAt: new Date().toISOString().slice(0, 16).replace("T", " "),
      source: "manual",
    };
    setMemories((prev) => [optimistic, ...prev]);
    setNewFact("");
    try {
      await asterPost("/api/memory", { text });
    } catch {
      // keep the optimistic entry; next reload will show real state
    }
  };

  const forget = (memory: MemoryFact) => {
    setMemories((prev) => prev.filter((m) => m.id !== memory.id));
    void asterPost("/api/memory/delete", { text: memory.text });
  };

  return (
    <motion.div variants={staggerContainer} initial="initial" animate="animate">
      <motion.div variants={staggerItem}>
        <SectionHeader
          title="Memory"
          description="What Aster remembers about you, across every conversation."
        />
      </motion.div>

      <motion.div variants={staggerItem} className="mb-4 flex gap-2">
        <div className="relative flex-1">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant/50"
          />
          <Input
            className="pl-9"
            placeholder="Search memories…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </motion.div>

      <motion.form
        variants={staggerItem}
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void addFact();
        }}
      >
        <Input
          placeholder="Teach Aster something new…"
          value={newFact}
          onChange={(e) => setNewFact(e.target.value)}
        />
        <Button type="submit" disabled={!newFact.trim()}>
          Save
        </Button>
      </motion.form>

      {error && (
        <p className="mb-3 text-sm text-error-text">{error}</p>
      )}

      <AnimatePresence mode="wait">
        {loading ? (
          <motion.div key="loading" variants={fade} initial="initial" animate="animate" exit="exit">
            <p className="text-on-surface-variant/50 text-sm">Loading memories…</p>
          </motion.div>
        ) : memories.length === 0 ? (
          <motion.div key="empty" variants={fade} initial="initial" animate="animate" exit="exit">
            <EmptyState icon={Brain} title="No memories found" description="Try a different search term." />
          </motion.div>
        ) : (
          <motion.div key="list" variants={fade} initial="initial" animate="animate" exit="exit">
            <Card>
              <ul className="divide-y divide-outline-custom">
                <AnimatePresence mode="popLayout" initial={false}>
                  {memories.map((memory) => {
                    const style = SOURCE_STYLE[memory.source];
                    return (
                      <motion.li
                        key={memory.id}
                        layout
                        variants={rowVariants}
                        initial="initial"
                        animate="animate"
                        exit="exit"
                        className="overflow-hidden"
                      >
                        <div className="flex items-center justify-between gap-3 py-3">
                          <div className="flex items-start gap-3">
                            <div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${style.classes}`}>
                              <style.icon size={14} />
                            </div>
                            <div>
                              <p className="text-body-md text-on-surface">{memory.text}</p>
                              <p className="mt-1 text-xs text-on-surface-variant/50">
                                {memory.source} · {memory.savedAt}
                              </p>
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => forget(memory)}
                            className="rounded-input p-2 text-on-surface-variant/60 transition-colors hover:bg-surface-elevated hover:text-error-text"
                            aria-label={`Forget: ${memory.text}`}
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                      </motion.li>
                    );
                  })}
                </AnimatePresence>
              </ul>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
