"""E2E: browse_web through the real brain's tool schema (llama-server must be up).

Drives one full tool round exactly as production does:
  1. import core.brain for the real system prompt + ADMIN_TOOLS
  2. ask llama-server with the user message
  3. execute the returned tool call(s) via tools.dom.browse
  4. feed the page text back and print Aster's final answer

Usage (repo root, llama-server healthy):
  python engine_testing/e2e_browse_web.py
  python engine_testing/e2e_browse_web.py "how much is a new ball on amazon.eg?"
"""
import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

URL = "http://localhost:8080/v1/chat/completions"
DEFAULT_USER = "[Mood: neutral] Can u check the prices of potatoes on amazon.eg?"


def _ask(messages, tools, model="Qwen3.6-35B-A3B-UD-IQ4_XS"):
    r = requests.post(URL, json={"model": model, "messages": messages, "tools": tools,
                                 "tool_choice": "auto", "temperature": 1.0}, timeout=240)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def main():
    import core.brain as brain  # heavy import on purpose — the real schema
    from tools.dom import browse, close_web_context

    user = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_USER
    tools = brain.ADMIN_TOOLS
    messages = [{"role": "system", "content": brain.messages[0]["content"]},
                {"role": "user", "content": user}]
    try:
        m = _ask(messages, tools)
        calls = m.get("tool_calls") or []
        print("round 1 tool_calls:", json.dumps(
            [{"name": c["function"]["name"], "args": c["function"].get("arguments")}
             for c in calls], ensure_ascii=False))
        if not calls:
            print("=== ANSWER (no tool) ===\n", (m.get("content") or "")[:400])
            return 0

        messages.append({"role": "assistant", "content": m.get("content") or None,
                         "tool_calls": calls})
        for i, c in enumerate(calls):
            name = c["function"]["name"]
            args = json.loads(c["function"].get("arguments") or "{}")
            if name == "browse_web":
                res = browse(url=args.get("url", ""), query=args.get("query", ""),
                             site=args.get("site", ""), max_chars=5000)
                text = (f"[Source: browse_web | {res['url']}]\nTitle: {res['title']}\n\n{res['text']}"
                        if res.get("ok") else f"FAILED — {res.get('error')}")
            else:
                text = f"(e2e harness does not execute {name})"
            messages.append({"role": "tool",
                             "tool_call_id": c.get("id") or f"call_{i}", "content": text})

        m2 = _ask(messages, tools)
        print("\n=== ANSWER ===\n" + (m2.get("content") or ""))
        return 0
    finally:
        close_web_context()


if __name__ == "__main__":
    sys.exit(main())
