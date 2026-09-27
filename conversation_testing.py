"""
conversation_testing.py — Aster speech-style sweep harness (gogi persona)
=========================================================================

Purpose
-------
Aster's *speaking style* is the thing under test, not his correctness. This
script sweeps several llama-server sampling presets against a fixed battery of
conversation prompts designed around the `gogi.md` best-friend persona, so you
can eyeball — side by side — which temperature / repeat_penalty / min_p combo
makes him sound the most natural.

It deliberately mirrors the real pipeline's request path:
  - same endpoint:  POST http://localhost:8080/v1/chat/completions
  - same persona loading: gogi.md with <!-- ... --> comments stripped
    (identical to core.brain._load_system_prompt)
  - same OpenAI chat message shape (system + user/assistant turns)

It does NOT import core.brain (which would trigger heavy vision/emotion/voice
model loads). The XML tool manual is intentionally omitted — tools barely affect
conversational tone, and this keeps the script standalone (only needs `requests`).

The dynamic context tags the live engine injects — `CURRENT CONTEXT`,
`[Mood: <state>]`, `[Speaker: <name>]` — are reproduced in the prompts so the
persona's mood-adaptive rules actually fire.

Prerequisite
------------
llama-server must already be running on localhost:8080 (start.bat), serving the
same model build Aster uses. No other Aster services are needed.

Run
---
    python conversation_testing.py
    python conversation_testing.py --presets recommended,tight      # subset
    python conversation_testing.py --prompts down,stressed,roast    # subset
    python conversation_testing.py --persona jarvis                 # other persona

Output
------
Aster_Vault/conversation-tests/conversation-test_<timestamp>.md   (human-readable matrix)
Aster_Vault/conversation-tests/conversation-test_<timestamp>.json (raw, for parsing)
"""

import argparse
import datetime
import json
import os
import re
import sys
import time

import requests

# --------------------------------------------------------------------------- #
# Paths — resolved relative to this file so it works regardless of cwd.
# --------------------------------------------------------------------------- #
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPTS_DIR = os.path.join(REPO_ROOT, "Aster_Vault", "System_Prompts")
OUTPUT_DIR = os.path.join(REPO_ROOT, "Aster_Vault", "conversation-tests")
API_URL = "http://localhost:8080/v1/chat/completions"
REQUEST_TIMEOUT = 120

# --------------------------------------------------------------------------- #
# Sampling presets — each is a full sampler config sent to llama-server.
# `repeat_penalty` and `min_p` are NOT sent by the live engine today; they are
# the main untested levers, so most presets exercise them. `baseline` reproduces
# exactly what config.py sends right now (the control).
#
# A value of None means "omit this key from the payload" (let the server default
# apply). For min_p, omitting it disables min-p sampling; for top_p, 1.0 disables
# nucleus filtering so min_p can act alone.
# --------------------------------------------------------------------------- #
PRESETS = {
    # Control: identical to config.py defaults (temp 1.0 / top_p 0.95 / top_k 64,
    # no repeat_penalty, no min_p). This is "how Aster sounds today."
    "baseline": {
        "temperature": 1.0, "top_p": 0.95, "top_k": 64,
        "repeat_penalty": None, "min_p": None,
    },
    # The two-lever fix discussed: cooler temp + light repetition penalty + min_p.
    "recommended": {
        "temperature": 0.75, "top_p": 0.95, "top_k": 64,
        "repeat_penalty": 1.1, "min_p": 0.05,
    },
    # Tighter / more deterministic — good if baseline rambles or repeats hard.
    "tight": {
        "temperature": 0.7, "top_p": 0.9, "top_k": 40,
        "repeat_penalty": 1.15, "min_p": 0.05,
    },
    # Keeps some warmth/spontaneity but reins in repetition. A middle ground.
    "loose_penalized": {
        "temperature": 0.85, "top_p": 0.95, "top_k": 64,
        "repeat_penalty": 1.1, "min_p": 0.05,
    },
    # Pure min-p sampling: top_p disabled (1.0), top_k off (0), min_p does the
    # truncation. Often the most natural for chatty models.
    "min_p_only": {
        "temperature": 0.85, "top_p": 1.0, "top_k": 0,
        "repeat_penalty": 1.1, "min_p": 0.07,
    },
    # Isolate the single biggest suspected fix: baseline + repeat_penalty only.
    "repeat_only": {
        "temperature": 1.0, "top_p": 0.95, "top_k": 64,
        "repeat_penalty": 1.1, "min_p": None,
    },
}

# --------------------------------------------------------------------------- #
# Test prompts — built for the gogi persona's documented behaviors.
#
# Each prompt is a list of OpenAI-format turns (the system prompt is prepended
# at request time). Context tags (CURRENT CONTEXT / [Mood: X]) are embedded the
# same way the live engine injects them. `expect` is a free-text note describing
# what good output looks like — printed in the report to guide judging, not
# auto-checked.
# --------------------------------------------------------------------------- #
PROMPTS = {
    "hype": {
        "desc": "Win / hyped — should go UP, celebrate first, one follow-up. 2 sentences.",
        "expect": "Loud genuine hype, matches his high, asks how it felt. No emojis. <=2 sentences.",
        "turns": [
            {"role": "user", "content": "[Mood: happy]\nI closed my first sale today — only one in my whole batch."},
        ],
    },
    "down": {
        "desc": "Low / lonely — presence before solutions, don't rush to fix.",
        "expect": "Sits with him, offers company or talk, no fixing, gentle. <=2 sentences.",
        "turns": [
            {"role": "user", "content": "[Mood: sad]\nidk man. feeling kinda low today."},
        ],
    },
    "stressed": {
        "desc": "Scared / overwhelmed — acknowledge weight, ground it, offer real help.",
        "expect": "Calms, asks for the real number, reminds he's done it before, offers to map it out.",
        "turns": [
            {"role": "user", "content": "[Mood: anxious]\nI'm so scared I won't finish these papers on time."},
        ],
    },
    "tired_late": {
        "desc": "Tired, 2am — keep it short and gentle, steer to rest, no lecture.",
        "expect": "Short, warm, 'go crash', no big conversation. <=2 sentences.",
        "turns": [
            {"role": "user", "content": "CURRENT CONTEXT: Tuesday, 02:14 AM.\n[Mood: neutral]\ni'm passing out man, night."},
        ],
    },
    "banter": {
        "desc": "Neutral chatting — just be the homie, no manufactured emotion.",
        "expect": "Loose, warm, curious, banter energy. Not over-hyped. <=2 sentences.",
        "turns": [
            {"role": "user", "content": "[Mood: neutral]\nyo what's good"},
        ],
    },
    "roast": {
        "desc": "Procrastination — call it directly, with love.",
        "expect": "Honest dig + a real question about what's blocking him. Not mean. <=2 sentences.",
        "turns": [
            {"role": "user", "content": "[Mood: neutral]\nok so I still haven't started that project lol"},
        ],
    },
    "emoji_bait": {
        "desc": "No-Emoji Law stress test — he explicitly asks for emojis.",
        "expect": "Hype WITHOUT a single emoji (hard law). <=2 sentences.",
        "turns": [
            {"role": "user", "content": "[Mood: happy]\nhype me up for leg day, throw some emojis in it!!"},
        ],
    },
    "locked_in": {
        "desc": "Locked-in gear — real code request, switch to dense/competent.",
        "expect": "Delivers actual correct Python. Brief in-voice intro, code block doesn't count vs sentence law.",
        "turns": [
            {"role": "user", "content": "[Mood: neutral]\nyo write me a python function that reverses a linked list, iteratively"},
        ],
    },
    "multiturn": {
        "desc": "Context carry — follow-up that needs the prior turn.",
        "expect": "Tracks the thread, stays in voice, doesn't re-introduce. <=2 sentences.",
        "turns": [
            {"role": "user", "content": "[Mood: neutral]\nman my code keeps crashing on startup"},
            {"role": "assistant", "content": "ugh that's the worst, what's the actual error it throws? paste it and let's kill it."},
            {"role": "user", "content": "[Mood: frustrated]\nModuleNotFoundError: No module named 'requests'"},
        ],
    },
    "filler_bait": {
        "desc": "Corporate-filler trap — a generic ask that tempts 'I'd be happy to help'.",
        "expect": "Just answers/helps, no 'Great question', no 'As an AI', no parroting. <=2 sentences.",
        "turns": [
            {"role": "user", "content": "[Mood: neutral]\ncan you help me out with something real quick"},
        ],
    },
    "repetition_stress": {
        "desc": "Open-ended longer ask — surfaces repetition/rambling under each sampler.",
        "expect": "Stays tight and varied, no looping phrases, holds the 2-sentence law.",
        "turns": [
            {"role": "user", "content": "[Mood: happy]\nbro pump me up, I'm about to go lock in for a 4 hour grind session"},
        ],
    },
}


# --------------------------------------------------------------------------- #
# Persona loading — byte-for-byte match to core.brain._load_system_prompt.
# --------------------------------------------------------------------------- #
def load_persona(name: str) -> str:
    path = os.path.join(SYSTEM_PROMPTS_DIR, f"{name}.md")
    with open(path, "r", encoding="utf-8") as f:
        body = f.read()
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    return body.strip()


# --------------------------------------------------------------------------- #
# Completion — same endpoint & shape as _execute_llm_completion, but with the
# extra sampler knobs threaded through.
# --------------------------------------------------------------------------- #
def run_completion(system_prompt: str, turns: list[dict], preset: dict,
                   n_predict: int = 1000) -> tuple[str, float]:
    messages = [{"role": "system", "content": system_prompt}] + [
        dict(t) for t in turns
    ]
    payload = {
        "model": "local",
        "messages": messages,
        "temperature": preset["temperature"],
        "top_p": preset["top_p"],
        "top_k": preset["top_k"],
        "max_tokens": n_predict,
        "stream": False,
    }
    # Only send the optional knobs when set, so omission == server default.
    if preset.get("repeat_penalty") is not None:
        payload["repeat_penalty"] = preset["repeat_penalty"]
    if preset.get("min_p") is not None:
        payload["min_p"] = preset["min_p"]

    t0 = time.time()
    resp = requests.post(API_URL, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    elapsed = time.time() - t0
    content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    return content, elapsed


# --------------------------------------------------------------------------- #
# Style heuristics — cheap, regex-based flags to make the matrix scannable.
# These don't judge quality, they catch hard gogi-rule breaks so you can skip
# obviously-broken outputs and focus on the close calls.
# --------------------------------------------------------------------------- #
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿️]"
)
_FILLER_RE = re.compile(
    r"\b(as an ai|i'd be happy to help|i am happy to help|great question|"
    r"certainly|i understand how you feel|i'm just an? (ai|assistant))\b",
    re.IGNORECASE,
)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)


def count_prose_sentences(text: str) -> int:
    """Sentence count over prose only — code blocks are stripped (the gogi
    two-sentence law explicitly exempts code/tool output/numbered steps)."""
    prose = _CODE_BLOCK_RE.sub("", text)
    # Drop numbered/bulleted list lines (also exempt).
    prose = "\n".join(
        ln for ln in prose.splitlines()
        if not re.match(r"\s*(\d+[.)]|[-*])\s", ln)
    )
    chunks = re.split(r"(?<=[.!?])\s+", prose.strip())
    return len([c for c in chunks if c.strip()])


def style_flags(text: str) -> list[str]:
    flags = []
    if _EMOJI_RE.search(text):
        flags.append("EMOJI")
    if _FILLER_RE.search(text):
        flags.append("FILLER")
    if re.search(r"\bmood\b", text, re.IGNORECASE):
        flags.append("SAID-MOOD")
    n = count_prose_sentences(text)
    if n > 2:
        flags.append(f"{n}-SENTENCES")
    if not text.strip():
        flags.append("EMPTY")
    return flags


# --------------------------------------------------------------------------- #
# Report writing.
# --------------------------------------------------------------------------- #
def write_reports(persona_name: str, results: dict, preset_names: list[str],
                  prompt_names: list[str], stamp: str) -> tuple[str, str]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    md_path = os.path.join(OUTPUT_DIR, f"conversation-test_{stamp}.md")
    json_path = os.path.join(OUTPUT_DIR, f"conversation-test_{stamp}.json")

    # ---- JSON (raw) ----
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "persona": persona_name,
                "timestamp": stamp,
                "presets": {p: PRESETS[p] for p in preset_names},
                "results": results,
            },
            f, indent=2, ensure_ascii=False,
        )

    # ---- Markdown (side-by-side matrix) ----
    lines = []
    lines.append(f"# Aster conversation test — `{persona_name}` persona")
    lines.append("")
    lines.append(f"- **Generated:** {stamp}")
    lines.append(f"- **Endpoint:** {API_URL}")
    lines.append(f"- **Presets ({len(preset_names)}):** {', '.join(preset_names)}")
    lines.append(f"- **Prompts ({len(prompt_names)}):** {', '.join(prompt_names)}")
    lines.append("")
    lines.append("Style flags are heuristic hard-rule breaks (EMOJI / FILLER / "
                 "SAID-MOOD / N-SENTENCES / EMPTY), not quality scores. Code "
                 "blocks & list items are exempt from the sentence count, "
                 "matching the gogi two-sentence law.")
    lines.append("")

    # Sampler legend.
    lines.append("## Sampler presets")
    lines.append("")
    lines.append("| preset | temp | top_p | top_k | repeat_penalty | min_p |")
    lines.append("|---|---|---|---|---|---|")
    for p in preset_names:
        s = PRESETS[p]
        rp = "—" if s.get("repeat_penalty") is None else s["repeat_penalty"]
        mp = "—" if s.get("min_p") is None else s["min_p"]
        lines.append(f"| `{p}` | {s['temperature']} | {s['top_p']} | "
                     f"{s['top_k']} | {rp} | {mp} |")
    lines.append("")

    # Per-prompt sections.
    for prompt_name in prompt_names:
        meta = PROMPTS[prompt_name]
        lines.append("---")
        lines.append("")
        lines.append(f"## Prompt: `{prompt_name}`")
        lines.append("")
        lines.append(f"_{meta['desc']}_")
        lines.append("")
        lines.append(f"**Looking for:** {meta['expect']}")
        lines.append("")
        lines.append("**Conversation sent:**")
        lines.append("")
        for turn in meta["turns"]:
            role = turn["role"]
            content = turn["content"].replace("\n", "  \n")
            lines.append(f"> **{role}:** {content}")
        lines.append("")

        for preset_name in preset_names:
            r = results[prompt_name][preset_name]
            flag_str = " ".join(f"`{x}`" for x in r["flags"]) or "`clean`"
            lines.append(f"### `{preset_name}` — {flag_str} "
                         f"({r['elapsed']:.1f}s)")
            lines.append("")
            if r.get("error"):
                lines.append(f"> ERROR: {r['error']}")
            else:
                # Render the reply verbatim in a blockquote.
                reply = r["reply"] or "(empty response)"
                for ln in reply.splitlines() or [""]:
                    lines.append(f"> {ln}" if ln else ">")
            lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return md_path, json_path


# --------------------------------------------------------------------------- #
# Main.
# --------------------------------------------------------------------------- #
def parse_args():
    ap = argparse.ArgumentParser(description="Aster speech-style sweep harness.")
    ap.add_argument("--persona", default="gogi",
                    help="Persona stem under Aster_Vault/System_Prompts (default: gogi).")
    ap.add_argument("--presets", default="",
                    help="Comma-separated preset subset (default: all). "
                         f"Available: {', '.join(PRESETS)}.")
    ap.add_argument("--prompts", default="",
                    help="Comma-separated prompt subset (default: all). "
                         f"Available: {', '.join(PROMPTS)}.")
    ap.add_argument("--max-tokens", type=int, default=1000,
                    help="max_tokens per completion (default: 1000).")
    return ap.parse_args()


def main():
    args = parse_args()

    preset_names = (
        [p.strip() for p in args.presets.split(",") if p.strip()]
        if args.presets else list(PRESETS)
    )
    prompt_names = (
        [p.strip() for p in args.prompts.split(",") if p.strip()]
        if args.prompts else list(PROMPTS)
    )

    bad_presets = [p for p in preset_names if p not in PRESETS]
    bad_prompts = [p for p in prompt_names if p not in PROMPTS]
    if bad_presets:
        sys.exit(f"Unknown preset(s): {bad_presets}. Available: {list(PRESETS)}")
    if bad_prompts:
        sys.exit(f"Unknown prompt(s): {bad_prompts}. Available: {list(PROMPTS)}")

    # Load persona.
    try:
        system_prompt = load_persona(args.persona)
    except OSError as e:
        sys.exit(f"Could not load persona '{args.persona}': {e}")
    print(f"[setup] Persona '{args.persona}' loaded ({len(system_prompt)} chars).")

    # Connectivity preflight — fail fast with a clear message.
    try:
        requests.get("http://localhost:8080/health", timeout=5)
    except requests.exceptions.RequestException:
        print("[warn] Could not reach llama-server /health on localhost:8080. "
              "Make sure start.bat is running. Continuing anyway...")

    total = len(prompt_names) * len(preset_names)
    print(f"[run] {len(prompt_names)} prompts x {len(preset_names)} presets "
          f"= {total} completions.\n")

    results = {pn: {} for pn in prompt_names}
    done = 0
    for prompt_name in prompt_names:
        meta = PROMPTS[prompt_name]
        for preset_name in preset_names:
            done += 1
            print(f"  [{done}/{total}] {prompt_name} :: {preset_name} ... ",
                  end="", flush=True)
            entry = {"reply": "", "flags": [], "elapsed": 0.0, "error": None}
            try:
                reply, elapsed = run_completion(
                    system_prompt, meta["turns"], PRESETS[preset_name],
                    n_predict=args.max_tokens,
                )
                entry["reply"] = reply
                entry["elapsed"] = elapsed
                entry["flags"] = style_flags(reply)
                flag_note = ",".join(entry["flags"]) or "clean"
                print(f"{elapsed:.1f}s [{flag_note}]")
            except Exception as e:
                entry["error"] = str(e)
                print(f"ERROR: {e}")
            results[prompt_name][preset_name] = entry

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    md_path, json_path = write_reports(
        args.persona, results, preset_names, prompt_names, stamp
    )

    # Summary: flag tally per preset, to point at the cleanest sampler.
    print("\n[summary] hard-rule flags per preset (lower = cleaner):")
    for preset_name in preset_names:
        tally = sum(len(results[pn][preset_name]["flags"]) for pn in prompt_names)
        errs = sum(1 for pn in prompt_names if results[pn][preset_name]["error"])
        print(f"  {preset_name:<16} flags={tally}  errors={errs}")

    print(f"\n[done] Markdown: {md_path}")
    print(f"[done] JSON:     {json_path}")


if __name__ == "__main__":
    main()
