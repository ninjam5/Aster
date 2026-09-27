import os
import sys
import tempfile

import numpy as np
import soundfile as sf
import torch
from huggingface_hub import hf_hub_download
from kokoro import KPipeline


LANG_CODE = "a"
DEFAULT_SPEED = 1.0
DEFAULT_OUTPUT_SR = 24000


def _voice_file_path(name_or_path: str) -> str:
    """Resolve a voice tensor path from a local file or Kokoro voices/<name>.pt."""
    candidate = name_or_path.strip()
    if os.path.isfile(candidate):
        return candidate

    if candidate.endswith(".pt") and os.path.isfile(candidate):
        return candidate

    voice_name = candidate[:-3] if candidate.endswith(".pt") else candidate
    return hf_hub_download(repo_id="hexgrad/Kokoro-82M", filename=f"voices/{voice_name}.pt")


def blend_voices(voice_a: str, weight_a: float, voice_b: str, out_path: str) -> str:
    """Create a blended voice tensor and save it to out_path."""
    if not (0.0 <= weight_a <= 1.0):
        raise ValueError("weight_a must be between 0.0 and 1.0")

    path_a = _voice_file_path(voice_a)
    path_b = _voice_file_path(voice_b)

    tensor_a = torch.load(path_a, weights_only=True)
    tensor_b = torch.load(path_b, weights_only=True)

    if tensor_a.shape != tensor_b.shape:
        raise ValueError(f"voice tensors are incompatible: {tensor_a.shape} vs {tensor_b.shape}")

    weight_b = 1.0 - weight_a
    blended = (tensor_a * weight_a) + (tensor_b * weight_b)
    torch.save(blended, out_path)
    return out_path


def synthesize_to_wav(pipeline: KPipeline, text: str, voice: str, out_wav: str, speed: float = DEFAULT_SPEED) -> str:
    """Synthesize text with Kokoro and save a single WAV file."""
    generator = pipeline(text, voice=voice, speed=speed)
    chunks = [audio for _, _, audio in generator if audio is not None]
    if not chunks:
        raise RuntimeError("Kokoro returned no audio chunks")

    audio = np.concatenate(chunks).astype(np.float32)
    sf.write(out_wav, audio, DEFAULT_OUTPUT_SR)
    return out_wav


def play_wav(path: str) -> None:
    """Play a WAV file using platform-default methods."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
        return

    # Linux / macOS fallback best-effort.
    if sys.platform == "darwin":
        os.system(f'open "{path}"')
    else:
        os.system(f'xdg-open "{path}"')


def print_help() -> None:
    print(
        "\nCommands:\n"
        "  /help\n"
        "  /voice <voice_name_or_path>\n"
        "  /speed <float>\n"
        "  /blend <voiceA> <weightA_0to1> <voiceB> <output_pt>\n"
        "  /test <voice_name_or_path> <sentence...>\n"
        "  /quit\n"
        "\nAny other input is spoken with current voice settings."
    )


def main() -> None:
    print("[Kokoro Test] Booting pipeline...")
    pipeline = KPipeline(lang_code=LANG_CODE)

    current_voice = "aster.pt" if os.path.isfile("aster.pt") else "af_bella"
    current_speed = DEFAULT_SPEED

    print(f"[Kokoro Test] Ready. Voice={current_voice}, speed={current_speed}")
    print_help()

    while True:
        try:
            user_input = input("\nSentence or command> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Kokoro Test] Exiting.")
            break

        if not user_input:
            continue

        if user_input.lower() == "/quit":
            print("[Kokoro Test] Exiting.")
            break

        if user_input.lower() == "/help":
            print_help()
            continue

        if user_input.startswith("/voice "):
            current_voice = user_input.split(" ", 1)[1].strip()
            print(f"[Kokoro Test] Active voice set to: {current_voice}")
            continue

        if user_input.startswith("/speed "):
            try:
                current_speed = float(user_input.split(" ", 1)[1].strip())
                print(f"[Kokoro Test] Speed set to: {current_speed}")
            except ValueError:
                print("[Kokoro Test] Invalid speed. Example: /speed 0.9")
            continue

        if user_input.startswith("/blend "):
            parts = user_input.split(" ")
            if len(parts) != 5:
                print("[Kokoro Test] Usage: /blend <voiceA> <weightA_0to1> <voiceB> <output_pt>")
                continue

            _, voice_a, weight_str, voice_b, output_pt = parts
            try:
                weight_a = float(weight_str)
                out = blend_voices(voice_a, weight_a, voice_b, output_pt)
                print(f"[Kokoro Test] Blended voice saved to: {out}")
            except Exception as e:
                print(f"[Kokoro Test] Blend failed: {e}")
            continue

        if user_input.startswith("/test "):
            parts = user_input.split(" ", 2)
            if len(parts) < 3:
                print("[Kokoro Test] Usage: /test <voice_name_or_path> <sentence...>")
                continue

            _, voice_for_test, sentence = parts
            try:
                with tempfile.NamedTemporaryFile(prefix="kokoro_test_", suffix=".wav", delete=False) as tmp:
                    out_wav = tmp.name
                synthesize_to_wav(pipeline, sentence, voice_for_test, out_wav, speed=current_speed)
                print(f"[Kokoro Test] Synthesized with {voice_for_test}: {out_wav}")
                play_wav(out_wav)
            except Exception as e:
                print(f"[Kokoro Test] Voice test failed: {e}")
            continue

        # Default interactive TTS line.
        try:
            with tempfile.NamedTemporaryFile(prefix="kokoro_line_", suffix=".wav", delete=False) as tmp:
                out_wav = tmp.name
            synthesize_to_wav(pipeline, user_input, current_voice, out_wav, speed=current_speed)
            print(f"[Kokoro Test] Spoken using {current_voice}: {out_wav}")
            play_wav(out_wav)
        except Exception as e:
            print(f"[Kokoro Test] TTS failed: {e}")


if __name__ == "__main__":
    main()
