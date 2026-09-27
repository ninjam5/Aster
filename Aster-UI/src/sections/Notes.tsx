import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { Variants } from "framer-motion";
import { AlarmClock, FileText, Timer as TimerIcon, Trash2 } from "lucide-react";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { Button } from "../components/ui/Button";
import { MOCK_REMINDERS } from "../mock/notes";
import { asterGet, asterPost } from "../api/client";
import { easing, staggerContainer, staggerItem } from "../theme/motion";
import type { Note } from "../types";

const rowVariants: Variants = {
  initial: { opacity: 0, height: 0 },
  animate: { opacity: 1, height: "auto", transition: { duration: 0.25, ease: easing } },
  exit: { opacity: 0, height: 0, transition: { duration: 0.2, ease: easing } },
};

export function Notes() {
  const [draft, setDraft] = useState("");
  const [notes, setNotes] = useState<Note[]>([]);

  useEffect(() => {
    asterGet<Note[]>("/api/notes")
      .then((data) => setNotes(data))
      .catch(() => {/* backend offline */});
  }, []);

  const addNote = () => {
    const text = draft.trim();
    if (!text) return;
    const optimistic: Note = {
      id: `local-${Date.now()}`,
      text,
      createdAt: new Date().toISOString().slice(0, 16).replace("T", " "),
    };
    setNotes((prev) => [optimistic, ...prev]);
    setDraft("");
    void asterPost("/api/notes", { text });
  };

  const deleteNote = (note: Note) => {
    setNotes((prev) => prev.filter((n) => n.id !== note.id));
    void asterPost("/api/notes/delete", { text: note.text });
  };

  return (
    <motion.div variants={staggerContainer} initial="initial" animate="animate">
      <motion.div variants={staggerItem}>
        <SectionHeader title="Notes & Reminders" description="Quick thoughts, timers, and alarms." />
      </motion.div>

      <div className="grid grid-cols-3 gap-6">
        <motion.div variants={staggerItem} className="col-span-2">
          <form
            className="mb-4 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              addNote();
            }}
          >
            <Input
              placeholder="Jot something down…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <Button type="submit" disabled={!draft.trim()}>
              Add
            </Button>
          </form>

          <Card>
            <ul className="divide-y divide-outline-custom">
              <AnimatePresence initial={false}>
                {notes.map((note) => (
                  <motion.li
                    key={note.id}
                    layout
                    variants={rowVariants}
                    initial="initial"
                    animate="animate"
                    exit="exit"
                    className="overflow-hidden"
                  >
                    <div className="flex items-center justify-between gap-3 py-3">
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-blue/15 text-primary">
                          <FileText size={14} />
                        </div>
                        <div>
                          <p className="text-body-md text-on-surface">{note.text}</p>
                          <p className="mt-1 text-xs text-on-surface-variant/50">{note.createdAt}</p>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => deleteNote(note)}
                        className="rounded-input p-2 text-on-surface-variant/60 transition-colors hover:bg-surface-elevated hover:text-error-text shrink-0"
                        aria-label={`Delete note: ${note.text}`}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>
          </Card>
        </motion.div>

        <motion.div variants={staggerItem}>
          <Card>
            <h2 className="mb-3 font-label-lg text-label-lg text-on-surface">Upcoming</h2>
            <ul className="space-y-3">
              {MOCK_REMINDERS.map((reminder) => (
                <li key={reminder.id} className="flex items-center gap-3">
                  <div className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-elevated">
                    {reminder.kind === "alarm" ? (
                      <AlarmClock size={16} className="text-primary" />
                    ) : (
                      <TimerIcon size={16} className="text-primary" />
                    )}
                    {reminder.kind === "timer" && (
                      <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-primary" />
                    )}
                  </div>
                  <div>
                    <p className="text-body-md text-on-surface">{reminder.label}</p>
                    <p className="text-xs text-on-surface-variant/50">{reminder.time}</p>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        </motion.div>
      </div>
    </motion.div>
  );
}
