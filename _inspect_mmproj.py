"""One-shot inspector — dumps mmproj tensor layout to identify audio vs vision."""
from gguf import GGUFReader
from collections import Counter

PATH = r"E:\LLM testing\Aster-localization\Aster_Vault\Models\mmproj-F16.gguf"
r = GGUFReader(PATH)

names = [t.name for t in r.tensors]
print(f"TOTAL TENSORS: {len(names)}")
print()

# Search for audio-side names
audio_terms = ("audio", "aud_", "mel", "whisper", "usm", "speech", "wav",
               "conformer", "stt", "spectrogram", "encoder.a")
vision_terms = ("vision", "vit", "siglip", "patch", "encoder.v", "image")

audio = [n for n in names if any(t in n.lower() for t in audio_terms)]
vision = [n for n in names if any(t in n.lower() for t in vision_terms)]

print(f"AUDIO-LIKE TENSORS: {len(audio)}")
for n in audio[:25]:
    print(f"  {n}")
if len(audio) > 25:
    print(f"  ... and {len(audio) - 25} more")
print()

print(f"VISION-LIKE TENSORS: {len(vision)}")
for n in vision[:10]:
    print(f"  {n}")
if len(vision) > 10:
    print(f"  ... and {len(vision) - 10} more")
print()

# Top-level name prefixes (first 2 dotted segments)
def prefix2(name):
    parts = name.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else parts[0]

print("TOP-LEVEL PREFIXES (first 2 segments):")
for p, c in Counter(prefix2(n) for n in names).most_common(30):
    print(f"  {c:4d}  {p}")
print()

# Architecture metadata
print("KEY METADATA FIELDS:")
for field in r.fields.values():
    name = field.name
    if any(k in name.lower() for k in ("arch", "audio", "vision", "clip",
                                         "projector", "modality", "mtmd")):
        try:
            val = field.contents()
        except Exception:
            val = "<binary>"
        if isinstance(val, str) and len(val) > 120:
            val = val[:120] + "..."
        print(f"  {name} = {val}")
