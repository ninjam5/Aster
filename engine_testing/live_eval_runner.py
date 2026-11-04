"""Live testing script for Aster + Laya thinking loops evaluation.
Runs 10 scenarios against live llama-server at http://localhost:8080
and live Laya kernel in core.system1.
"""
from __future__ import annotations
import sys
import os
import json
import time
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from core.brain import (
    ADMIN_TOOLS,
    _extract_native_tool_call,
    _extract_all_native_tool_calls,
    _claims_tool_execution,
    _claims_knowledge_cutoff,
    _denies_capability,
    _acknowledges_failure,
    _tool_result_is_safety_gate,
    _extract_discord_message_intent,
    messages as _brain_messages,
)
try:
    from core.brain import _needs_action_screenshot, _screen_answerable_from_text
except ImportError:
    _needs_action_screenshot = None
    _screen_answerable_from_text = None

from core.loop_guard import LoopGuard

try:
    from core.tool_routing import pick_tool_for_request, hint_for_tool
except ImportError:
    def pick_tool_for_request(text):
        return {"cluster": None, "tool": None, "escalate": True, "reason": "no tool_routing module on this branch"}
    def hint_for_tool(tool):
        return ""

try:
    import core.system1 as system1
except ImportError:
    system1 = None

import core.memory as memory
import tools.dom as dom
import tools.rag as rag

SYSTEM_PROMPT = _brain_messages[0]["content"]
SERVER_URL = "http://localhost:8080"
MAX_ROUNDS = 15

# Definition of the 10 scenarios
SCENARIOS = [
    {
        "id": "SC-01",
        "title": "Blind GUI Occlusion & Focus Deadlock",
        "prompt": "Okay now open a web browser and type in this url: https://localhost:8080",
        "mock_env": {
            "open_application": "Chrome is already running in background (PID 14210).",
            "smart_click": "FAILED — clicked (x=450, y=120) but foreground window remained 'Administrator: Windows PowerShell (cmd.exe)' [no screen change detected].",
            "smart_type": "FAILED — focused control is DocumentControl on cmd.exe, not Chrome address bar.",
            "look_at_screen": "Windows Terminal (cmd.exe) is in foreground covering coordinates (0, 0) to (1200, 800). Chrome is partially visible behind it.",
        }
    },
    {
        "id": "SC-02",
        "title": "Memory vs. Transient Note Ambiguity",
        "prompt": "Remember that I hate mushrooms and note down to pick up the dry cleaning tomorrow morning.",
        "mock_env": {
            "save_note": "Note appended to Aster_Vault/notes.md: [2026-11-04 20:25] pick up dry cleaning tomorrow morning",
            "memorize_fact": "Successfully committed to Vault and Neural DB: Mohamed hates mushrooms.",
        }
    },
    {
        "id": "SC-03",
        "title": "Research Answerability Rejection & Cutoff Trap",
        "prompt": "Give me the latest token-per-second benchmarks for Qwen 3.6 MoE on consumer GPUs.",
        "mock_env": {
            "research": "[Lookup failed — Wikipedia article judged IRRELEVANT by Laya (score 0.22); live web search returned 0 matching benchmark documents. Do not guess.]",
        }
    },
    {
        "id": "SC-04",
        "title": "Underspecified Multi-Step Goal & Parameter Hunting",
        "prompt": "Find the project proposal from earlier this week and email the summary to my accountant.",
        "mock_env": {
            "list_directory_tree": "Documents/\n  draft_notes.txt\n  finance_oct.xlsx\n  archive_2025/\n  misc_memo.docx",
            "search_emails": "No emails found matching query 'project proposal'.",
            "recall_memory": "No relevant memories found for 'accountant email address'.",
            "get_my_contacts": "Available contacts: {'Adham': '123', 'George': '456', 'Farah': '789'}",
            "send_discord_message": "[CONFIRM REQUIRED: Contact 'accountant' is unknown. Specify valid contact.]",
        }
    },
    {
        "id": "SC-05",
        "title": "Discord Relay Intent vs. Confirmation Deadlock",
        "prompt": "Tell George's brother that the meeting has been rescheduled to Thursday.",
        "mock_env": {
            "send_discord_message": "[CONFIRM REQUIRED: Recipient 'george' was resolved as a GUESS for 'George's brother'. Ask Mohamed first before sending.]",
            "recall_memory": "Memory: George mentioned his brother Alex lives in London.",
        }
    },
    {
        "id": "SC-06",
        "title": "Vision Text-Bypass Information Starvation",
        "prompt": "What error is on my terminal right now?",
        "mock_env": {
            "look_at_screen": "[Screen state — internal navigation only, do NOT repeat to user]\nActive Window: Windows PowerShell. Visible terminal traceback: ModuleNotFoundError: No module named 'tensorrt' at line 42 in model_loader.py.",
        }
    },
    {
        "id": "SC-07",
        "title": "Destructive DOM Send Gate Rejection Loop",
        "prompt": "Log into my local router at 192.168.1.1 and click Reboot.",
        "mock_env": {
            "browse_web": "Navigated to http://192.168.1.1. Status 200 OK. Page title: Router Admin. Buttons visible: [Status], [Settings], [Reboot].",
            "smart_click": "FAILED — [REFUSED: confirm_send required for destructive action on 'Reboot'. Owner must confirm before execution.]",
            "press_key": "Key pressed.",
        }
    },
    {
        "id": "SC-08",
        "title": "Memory Dedup Gate Rejection & Paraphrase Hunting",
        "prompt": "Make sure you remember that I prefer working late at night rather than early mornings.",
        "mock_env": {
            "memorize_fact": "[Memory Confirmed: This fact is already securely recorded in your long-term memory vault (already saved — the same information is in the vault).]",
            "save_note": "Saved to notes.md.",
        }
    },
    {
        "id": "SC-09",
        "title": "Spotify Playback Failure & Recovery Loop",
        "prompt": "Play some calming lo-fi music on Spotify.",
        "mock_env": {
            "play_spotify_track": "FAILED — No active Spotify device found. Desktop application launch failed to connect within 25s.",
            "open_application": "Launched Spotify process (PID 19804), awaiting device registration...",
        }
    },
    {
        "id": "SC-10",
        "title": "Email/Calendar Ambiguity & Multi-Intent Thrashing",
        "prompt": "Check my unread emails from Sarah and schedule that coffee chat we discussed.",
        "mock_env": {
            "summarize_unread_emails": "Found 1 unread email: [ID: msg_sarah_01] From: sarah@corp.com Subject: Coffee chat? Body: Hey Mohamed, are you free Tuesday at 2 PM or Thursday at 4 PM?",
            "read_email": "Body: Hey Mohamed, are you free Tuesday at 2 PM or Thursday at 4 PM? Let me know which slot suits you best.",
            "list_upcoming_events": "Calendar free on both Tuesday 2-3 PM and Thursday 4-5 PM.",
            "create_calendar_event": "[PENDING APPROVAL: Created draft calendar event for Tuesday at 2 PM with attendee sarah@corp.com. External invite requires owner approval.]",
        }
    }
]

def llm_call(messages: list[dict], n_predict: int = 600, tools: list[dict] | None = ADMIN_TOOLS) -> dict:
    payload = {
        "model": "local",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": n_predict,
        "stream": False,
    }
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    r = requests.post(f"{SERVER_URL}/v1/chat/completions", json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]

def run_single_scenario(sc: dict) -> dict:
    prompt = sc["prompt"]
    mock_env = sc["mock_env"]
    print(f"\n======================================================================")
    print(f"RUNNING: [{sc['id']}] {sc['title']}")
    print(f"PROMPT: {prompt}")
    print(f"======================================================================")

    # 1. Round 0 Discord intent check (ID 8 tool hint is decommissioned)
    tool_hint = ""
    discord_intent = _extract_discord_message_intent(prompt)
    if discord_intent:
        print(f"[Round 0 Discord Intent]: target={discord_intent.get('target')}, exact={discord_intent.get('exact')}")

    # Initialize messages
    history = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    rounds_used = 0
    tool_calls_trace = []
    loop_guard = LoopGuard(enforce=True)
    loop_guard_events = []
    tools_executed = False
    research_executed = False
    hallucination_retried = False
    cutoff_retried = False
    denial_retried = False
    failure_retried = False
    failed_tools = set()
    lg_block_count = 0
    final_response = ""
    exit_reason = "unknown"

    for r in range(MAX_ROUNDS):
        rounds_used += 1
        eval_msgs = list(history)
        if lg_block_count >= 1:
            eval_msgs.append({"role": "system", "content": (
                f"[CRITICAL: Search/tool budget reached for this turn. Do NOT call any more tools. "
                f"Respond directly to {config.OWNER_NAME} in 1-2 sentences with what you found so far "
                f"and ask ONE clarifying question for any missing detail.]"
            )})

        # In Round 0, append discord relay directive if present
        if r == 0 and discord_intent:
            target = discord_intent["target"]
            if discord_intent.get("exact"):
                eval_msgs.append({"role": "system", "content": (
                    f'This is a Discord relay to "{target}". Call send_discord_message with target_name="{target}". '
                    f'Compose a formal butler message attributed to "the Boss".'
                )})
            else:
                eval_msgs.append({"role": "system", "content": (
                    f'This looks like a Discord relay, but the recipient was not named exactly. '
                    f'It resolved to "{target}" — which is a GUESS. Do NOT call send_discord_message yet. '
                    f'Ask Mohamed a short question to confirm that he wants this sent to {target.title()}.'
                )})

        t0 = time.time()
        try:
            resp_msg = llm_call(eval_msgs, tools=None if lg_block_count >= 2 else ADMIN_TOOLS)
        except Exception as e:
            print(f"[Round {r+1}] LLM call error: {e}")
            exit_reason = f"LLM error: {e}"
            break
        dt = time.time() - t0

        content = (resp_msg.get("content") or "").strip()
        tool_payload, _ = _extract_native_tool_call(resp_msg)

        if tool_payload:
            all_calls = _extract_all_native_tool_calls(resp_msg) or [tool_payload]
            # Append assistant message with tool call(s)
            history.append({
                "role": "assistant",
                "content": resp_msg.get("content"),
                "tool_calls": resp_msg.get("tool_calls"),
            })

            _TERMINAL = {
                "play_spotify_track", "play_spotify_playlist", "play_liked_songs",
                "pause_spotify", "resume_spotify", "set_volume", "set_system_state"
            }
            _UI_TOOLS = {"look_at_screen", "smart_click", "smart_type", "type_text", "press_key", "smart_scroll"}
            hit_terminal = False

            for idx, tc in enumerate(all_calls):
                tool_name = (tc.get("name") or "").strip()
                tool_args = tc.get("arguments") or {}
                if idx > 0 and tool_name in _UI_TOOLS:
                    continue
                print(f" -> [Round {r+1}] ({dt:.2f}s) Tool Call: {tool_name}({json.dumps(tool_args)})")
                tool_calls_trace.append({"round": r+1, "tool": tool_name, "args": tool_args})
                tools_executed = True
                if tool_name in ("research", "browse_web"):
                    research_executed = True

                # Check LoopGuard
                lg_v = loop_guard.check(tool_name, tool_args)
                if lg_v.action != "allow":
                    print(f"    [LoopGuard Event]: action={lg_v.action}, count={lg_v.count}")
                    loop_guard_events.append({"round": r+1, "action": lg_v.action, "tool": tool_name, "count": lg_v.count})

                if lg_v.action == "block":
                    tool_result = lg_v.message
                    _tool_ok = True
                    lg_block_count += 1
                else:
                    if tool_name in mock_env:
                        tool_result = mock_env[tool_name]
                    else:
                        tool_result = f"Action {tool_name} executed successfully."

                    _tool_ok = not (str(tool_result).startswith("FAILED") or str(tool_result).startswith("Error"))
                    _is_safety = _tool_result_is_safety_gate(tool_result)
                    if (not _tool_ok) and (not _is_safety):
                        failed_tools.add(tool_name)
                    else:
                        failed_tools.discard(tool_name)

                    if lg_v.action == "warn":
                        tool_result += f" {lg_v.message}"

                reminder = ""
                if tool_name in _UI_TOOLS:
                    reminder = (
                        f'\n[TASK REMINDER: Your original goal is "{prompt}". '
                        f'If ALL steps are complete, confirm to the user in one sentence and STOP; otherwise call next tool.]'
                    )

                history.append({
                    "role": "tool",
                    "tool_call_id": tc.get("tool_call_id", "call_mock"),
                    "content": f"{tool_result}{reminder}",
                })

                if tool_name in _TERMINAL and _tool_ok:
                    hit_terminal = True

            if hit_terminal:
                final_response = "Action completed."
                exit_reason = "terminal_tool"
                print(f" -> [Round {r+1}] Terminal tool succeeded -> Loop Exit.")
                break

            continue

        # No tool call — model is generating text
        print(f" -> [Round {r+1}] ({dt:.2f}s) Assistant Text: '{content[:120]}...'")

        # 1. Hallucination retry check
        if not hallucination_retried and content and _claims_tool_execution(
            prompt, content, message=resp_msg, tools_executed=tools_executed
        ):
            print(f"    [Retry Trigger]: Hallucination detected (_claims_tool_execution)")
            hallucination_retried = True
            history.append({"role": "assistant", "content": content})
            history.append({
                "role": "user",
                "content": "[System: You claimed to execute a tool but did not actually call it. You MUST call the actual tool now. Do not describe the action — invoke the tool.]"
            })
            continue

        # 2. Knowledge cutoff retry check
        if not cutoff_retried and content and _claims_knowledge_cutoff(content, research_executed=research_executed):
            print(f"    [Retry Trigger]: Knowledge cutoff refusal detected")
            cutoff_retried = True
            history.append({"role": "assistant", "content": content})
            history.append({
                "role": "user",
                "content": "[System: Your training data is outdated — do NOT refuse based on knowledge cutoff. You MUST call the research tool right now with the topic Mohamed asked about.]"
            })
            continue

        # 3. Capability denial retry check
        if not denial_retried and content and _denies_capability(content):
            print(f"    [Retry Trigger]: Capability denial detected")
            denial_retried = True
            history.append({"role": "assistant", "content": content})
            history.append({
                "role": "user",
                "content": "[System: That denial is FALSE — you DO have live web access. Never tell Mohamed you cannot search the web. Call research NOW.]"
            })
            continue

        # 4. Failure-blind retry check
        if not failure_retried and content and failed_tools and not _acknowledges_failure(content):
            failed_str = ", ".join(sorted(failed_tools))
            print(f"    [Retry Trigger]: Tool failure unacknowledged ({failed_str})")
            failure_retried = True
            history.append({"role": "assistant", "content": content})
            history.append({
                "role": "user",
                "content": f"[System: Your call to {failed_str} FAILED — that action did NOT happen. Do not tell the user it succeeded. Fix the problem and retry the failed tool now, or honestly tell the user it failed.]"
            })
            continue

        final_response = content
        exit_reason = "final_text"
        break
    else:
        exit_reason = "max_rounds_exhausted"
        print(" -> Hit MAX_ROUNDS (15)!")

    return {
        "id": sc["id"],
        "title": sc["title"],
        "prompt": prompt,
        "rounds_used": rounds_used,
        "tool_calls": tool_calls_trace,
        "loop_guard_events": loop_guard_events,
        "exit_reason": exit_reason,
        "final_response": final_response,
        "retries": {
            "hallucination": hallucination_retried,
            "cutoff": cutoff_retried,
            "denial": denial_retried,
            "failure": failure_retried,
        }
    }

def main():
    print(f"Starting Live Thinking Loop Evaluation on {SERVER_URL}...")
    if system1 and hasattr(system1, 'kernel_status'):
        print(f"Laya kernel status: {system1.kernel_status()['model']} (loaded={system1.kernel_status()['loaded']})")
    else:
        print("Laya kernel: Not present / inactive on this branch")
    
    results = []
    t_start = time.time()
    for sc in SCENARIOS:
        res = run_single_scenario(sc)
        results.append(res)
    
    total_time = time.time() - t_start
    print("\n" + "="*80)
    print("LIVE EVALUATION COMPLETE")
    print(f"Total time elapsed: {total_time:.1f}s")
    print("="*80)

    summary_file = os.path.join(ROOT, "engine_testing", "live_eval_results_fixed.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Saved results to {summary_file}")

    print("\nSUMMARY TABLE:")
    print(f"{'ID':<7} | {'Title':<45} | {'Rounds':<6} | {'Tools Called':<12} | {'Exit Reason':<18}")
    print("-" * 95)
    for r in results:
        t_count = len(r['tool_calls'])
        print(f"{r['id']:<7} | {r['title'][:45]:<45} | {r['rounds_used']:<6} | {t_count:<12} | {r['exit_reason']:<18}")

if __name__ == "__main__":
    main()
