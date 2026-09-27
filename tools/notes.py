import os
from datetime import datetime

NOTES_PATH = os.path.join("Aster_Vault", "notes.md")


def save_note(text: str) -> str:
    """Append a timestamped note to Aster_Vault/notes.md."""
    text = text.strip()
    if not text:
        return "Error: note text cannot be empty."

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- **{timestamp}** — {text}\n"

    os.makedirs("Aster_Vault", exist_ok=True)

    if not os.path.exists(NOTES_PATH):
        with open(NOTES_PATH, "w", encoding="utf-8") as f:
            f.write("# Aster Notes\n\n")

    with open(NOTES_PATH, "a", encoding="utf-8") as f:
        f.write(entry)

    return f"[Note saved: \"{text}\"]"


def get_notes() -> str:
    """Read and return all saved notes."""
    if not os.path.exists(NOTES_PATH):
        return "[No notes saved yet.]"

    try:
        with open(NOTES_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content if content else "[No notes saved yet.]"
    except Exception as e:
        return f"[Error reading notes: {e}]"


def delete_note(text: str) -> str:
    """Remove the first note whose text matches `text` (trimmed, exact)."""
    text = text.strip()
    if not text or not os.path.exists(NOTES_PATH):
        return "[Note not found.]"

    try:
        with open(NOTES_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        kept = []
        removed = False
        for line in lines:
            # Each note line: "- **timestamp** — text\n"
            # Extract the text after " — "
            if not removed and " — " in line:
                note_text = line.split(" — ", 1)[1].strip()
                if note_text == text:
                    removed = True
                    continue
            kept.append(line)

        if not removed:
            return "[Note not found.]"

        with open(NOTES_PATH, "w", encoding="utf-8") as f:
            f.writelines(kept)
        return f"[Note deleted: \"{text}\"]"
    except Exception as e:
        return f"[Error deleting note: {e}]"
