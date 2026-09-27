"""
Aster Engine Test Scenarios — pure data, no logic.

Each scenario dict keys:
  id              str          e.g. "C1-01"
  category        str          one of the category slugs below
  prompt          str          the user message to send
  expected_tool   str|None     tool name the model MUST call (None = no tool)
  expected_args   dict|None    arg key→expected value (str substring, int exact, or callable)
  expected_sequence list[str]  for multi-step: ordered subsequence of tool calls
  mock_results    dict         tool_name → canned result string (or callable(args)->str)
  mode            str          "butler"|"technical" for two_face tests
  vision          bool         True → attach the test screenshot as a multimodal message
  manual          bool         True → always goes into the Manual Review Queue
"""

SCENARIOS = [

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 1 — Single Tool Selection
    # Correct tool, valid tool call, right first choice.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C1-01", "category": "single_tool",
        "prompt": "What time is it?",
        "expected_tool": "get_current_time", "expected_args": {},
        "mock_results": {"get_current_time": "2026-06-18 14:32:00"},
    },
    {
        "id": "C1-02", "category": "single_tool",
        "prompt": "Pause Spotify.",
        "expected_tool": "pause_spotify", "expected_args": {},
        "mock_results": {"pause_spotify": "Spotify playback paused."},
    },
    {
        "id": "C1-03", "category": "single_tool",
        "prompt": "Set the volume to 40.",
        "expected_tool": "set_volume", "expected_args": {"level_percentage": 40},
        "mock_results": {"set_volume": "Volume set to 40%."},
    },
    {
        "id": "C1-04", "category": "single_tool",
        "prompt": "Lock my PC.",
        "expected_tool": "set_system_state",
        "expected_args": {"action": lambda v: str(v or "").lower() == "lock"},
        "mock_results": {
            "set_system_state": "PC locked successfully.",
            # Return a realistic payload so the model doesn't retry if it calls this
            "generate_kokoro_voice": "[NATIVE_AUDIO_PAYLOAD:mock_audio.wav]",
        },
    },
    {
        "id": "C1-05", "category": "single_tool",
        "prompt": "What song is playing on Spotify right now?",
        "expected_tool": "get_current_track", "expected_args": {},
        "mock_results": {"get_current_track": "Now playing: Bohemian Rhapsody by Queen on Spotify."},
    },
    {
        "id": "C1-06", "category": "single_tool",
        "prompt": "Skip this song.",
        "expected_tool": "skip_spotify_track", "expected_args": {},
        "mock_results": {"skip_spotify_track": "Skipped to next track."},
    },
    {
        "id": "C1-07", "category": "single_tool",
        "prompt": "Open Notepad for me.",
        "expected_tool": "open_application",
        "expected_args": {"app_name": "notepad"},
        "mock_results": {"open_application": "Notepad launched successfully."},
    },
    {
        "id": "C1-08", "category": "single_tool",
        "prompt": "Take a screenshot and describe what you see.",
        "expected_tool": "look_at_screen", "expected_args": {},
        "mock_results": {"look_at_screen": "Screenshot captured. Screen shows VS Code editor with a Python file open."},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 2 — Argument Extraction Precision
    # Model must parse exact numeric or string values from natural language.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C2-01", "category": "arg_precision",
        "prompt": "Set a timer for 5 minutes and 30 seconds.",
        "expected_tool": "set_timer",
        # Accept any valid unit combination for 5m 30s (330 s total).
        # Multi-field: minutes=5 + seconds=30.  Single-field: minutes=5.5 OR seconds=330.
        # Duration-string-only: minutes=None AND seconds=None (duration field carries it).
        # Individual lambdas can't cross-validate the total; they accept any value within
        # the valid range for each field.
        "expected_args": {
            "minutes": lambda v: v is None or 0 < float(v) <= 5.5,
            "seconds": lambda v: v is None or 0 < float(v) <= 330,
        },
        "mock_results": {"set_timer": "Timer set for 330 seconds (5m 30s)."},
    },
    {
        "id": "C2-02", "category": "arg_precision",
        "prompt": "Set an alarm for 7:30 AM tomorrow morning.",
        "expected_tool": "set_alarm",
        # allow_prereq: model may call get_current_time first to resolve "tomorrow" — correct
        # behaviour.  The tool_ok check should find set_alarm anywhere in the call list.
        "allow_prereq": True,
        "expected_args": {"time_str": lambda v: v is not None and ("7:30" in str(v) or "07:30" in str(v))},
        "mock_results": {
            "get_current_time": "2026-06-21 08:15:00",
            "set_alarm": "Alarm set for 07:30.",
        },
    },
    {
        "id": "C2-03", "category": "arg_precision",
        "prompt": "Play Bohemian Rhapsody by Queen on Spotify.",
        "expected_tool": "play_spotify_track",
        "expected_args": {"query": "bohemian"},
        "mock_results": {"play_spotify_track": "Now playing: Bohemian Rhapsody by Queen."},
    },
    {
        "id": "C2-04", "category": "arg_precision",
        "prompt": "Research the James Webb Space Telescope for me.",
        "expected_tool": "research",
        "expected_args": {"topic": "james webb"},
        "mock_results": {"research": (
            "The James Webb Space Telescope (JWST) is a space telescope designed to conduct "
            "infrared astronomy. Its primary mirror is 6.5 metres in diameter and it orbits "
            "the Sun–Earth L2 Lagrange point."
        )},
    },
    {
        "id": "C2-05", "category": "arg_precision",
        "prompt": "Turn the volume all the way down to 20 percent.",
        "expected_tool": "set_volume",
        "expected_args": {"level_percentage": 20},
        "mock_results": {"set_volume": "Volume set to 20%."},
    },
    {
        "id": "C2-06", "category": "arg_precision",
        "prompt": "Remind me in exactly 15 minutes to take my medication.",
        "expected_tool": "set_timer",
        "expected_args": {
            "minutes": lambda v: v is None or 0 <= float(v) <= 15,
            "seconds": lambda v: v is None or 0 <= float(v) <= 900,
        },
        "mock_results": {"set_timer": "Timer set for 900 seconds (15 minutes)."},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 3 — Multi-step Reasoning / Tool Chaining
    # Model must chain tools correctly to reach a final answer.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C3-01", "category": "multi_step",
        "prompt": "Find my notes file and tell me what's written in it.",
        "expected_sequence": ["list_directory_tree", "read_local_file"],
        "mock_results": {
            "list_directory_tree": (
                "Aster_Vault/\n"
                "  notes.md\n"
                "  memory.md\n"
                "  database/\n"
                "  images/"
            ),
            "read_local_file": (
                "# Mohamed's Notes\n"
                "- 2026-06-18: Buy groceries — eggs, milk, olive oil\n"
                "- 2026-06-17: Call the dentist before Thursday\n"
                "- 2026-06-15: Finish the Aster vision module"
            ),
        },
    },
    {
        "id": "C3-02", "category": "multi_step",
        "prompt": "Research quantum computing briefly and save a summary to a file called quantum_summary.txt on the desktop.",
        "expected_sequence": ["research", "write_local_file"],
        "mock_results": {
            "research": (
                "Quantum computing uses quantum-mechanical phenomena such as superposition and "
                "entanglement to process information. Unlike classical bits (0 or 1), qubits can "
                "exist in both states simultaneously. Leading hardware approaches include "
                "superconducting qubits (IBM, Google) and trapped ions (IonQ)."
            ),
            "write_local_file": "File written successfully: C:/Users/moham/Desktop/quantum_summary.txt",
        },
    },
    {
        "id": "C3-03", "category": "multi_step",
        "prompt": "Check what processes are running and tell me if Chrome is open.",
        "expected_sequence": ["list_running_processes"],
        "mock_results": {
            "list_running_processes": (
                "Running processes:\n"
                "  chrome.exe      PID 1234  RAM 512MB\n"
                "  notepad.exe     PID 5678  RAM 24MB\n"
                "  explorer.exe    PID 900   RAM 180MB\n"
                "  python.exe      PID 2048  RAM 1.2GB"
            ),
        },
    },
    {
        "id": "C3-04", "category": "multi_step",
        "prompt": "Look at my screen, find the search bar, and click it.",
        "expected_sequence": ["look_at_screen", "smart_click"],
        "mock_results": {
            "look_at_screen": (
                "Screen shows Chrome browser on Google homepage. "
                "A search bar is centered in the middle of the page with the text 'Search Google or type a URL'."
            ),
            "smart_click": "Clicked: search bar (center of screen).",
        },
    },
    {
        "id": "C3-05", "category": "multi_step",
        "prompt": "Play my liked songs on shuffle.",
        "expected_sequence": ["play_liked_songs"],
        "mock_results": {
            "play_liked_songs":   "Now playing your Liked Songs.",
            "shuffle_spotify":    "Shuffle enabled.",
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 4 — Restraint / Negative Tests
    # Model must NOT call any tool for conversational inputs.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C4-01", "category": "restraint",
        "prompt": "How are you today?",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C4-02", "category": "restraint",
        "prompt": "What do you think about jazz music?",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C4-03", "category": "restraint",
        "prompt": "Good morning.",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C4-04", "category": "restraint",
        "prompt": "Tell me a short joke.",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C4-05", "category": "restraint",
        "prompt": "That's very interesting, thank you.",
        "expected_tool": None, "mock_results": {},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 5 — Hallucination Resistance
    # Model must emit a real tool call rather than narrating the action.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C5-01", "category": "hallucination",
        "prompt": "Take a screenshot.",
        "expected_tool": "look_at_screen",
        "mock_results": {"look_at_screen": "Screenshot captured. Screen shows the Windows desktop."},
    },
    {
        "id": "C5-02", "category": "hallucination",
        "prompt": "Play Shape of You by Ed Sheeran.",
        "expected_tool": "play_spotify_track",
        "mock_results": {"play_spotify_track": "Now playing: Shape of You by Ed Sheeran."},
    },
    {
        "id": "C5-03", "category": "hallucination",
        "prompt": "Set a 10 minute timer right now.",
        "expected_tool": "set_timer",
        "mock_results": {"set_timer": "Timer set for 600 seconds (10 minutes)."},
    },
    {
        "id": "C5-04", "category": "hallucination",
        "prompt": "Open Chrome browser.",
        "expected_tool": "open_application",
        "mock_results": {"open_application": "Chrome launched."},
    },
    {
        "id": "C5-05", "category": "hallucination",
        "prompt": "Mute the system.",
        "expected_tool": "set_volume",
        "mock_results": {"set_volume": "Volume set to 0 (muted)."},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 6 — Knowledge-cutoff Routing
    # Model must call 'research' rather than refusing or hallucinating.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C6-01", "category": "cutoff_routing",
        "prompt": "What were the biggest tech announcements at CES 2026?",
        "expected_tool": "research",
        "mock_results": {"research": "CES 2026 featured announcements in AI hardware, AR glasses, and humanoid robots from several manufacturers."},
    },
    {
        "id": "C6-02", "category": "cutoff_routing",
        "prompt": "Who won the 2024 US presidential election?",
        "expected_tool": "research",
        "mock_results": {"research": "Donald Trump won the 2024 US presidential election, defeating Kamala Harris with 312 electoral votes."},
    },
    {
        "id": "C6-03", "category": "cutoff_routing",
        "prompt": "What are the latest breakthroughs in nuclear fusion energy?",
        "expected_tool": "research",
        "mock_results": {"research": "Recent fusion milestones include NIF achieving ignition and Commonwealth Fusion's SPARC magnet achieving 20 Tesla."},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 7 — Persona Adherence  (manual review + heuristic auto-flags)
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C7-01", "category": "persona", "manual": True,
        "prompt": "How are you feeling today?",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C7-02", "category": "persona", "manual": True,
        "prompt": "Am I boring you?",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C7-03", "category": "persona", "manual": True,
        "prompt": "What do you think about AI taking over the world?",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C7-04", "category": "persona", "manual": True,
        "prompt": "You're genuinely the best assistant I've ever used.",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C7-05", "category": "persona", "manual": True,
        "prompt": "What's your name and who made you?",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C7-06", "category": "persona", "manual": True,
        "prompt": "It's 3 AM and I still can't sleep.",
        "expected_tool": None, "mock_results": {},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 8 — Two-Face Protocol
    # Butler mode: ≤2 sentences. Technical mode: dense, accurate, no filler.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C8-01", "category": "two_face", "mode": "butler", "manual": True,
        "prompt": "What should I have for dinner tonight?",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C8-02", "category": "two_face", "mode": "technical", "manual": True,
        "prompt": (
            "Explain how KV-cache quantization works and why Q4_0 "
            "reduces VRAM usage in llama.cpp specifically."
        ),
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C8-03", "category": "two_face", "mode": "butler", "manual": True,
        "prompt": "Should I pull an all-nighter to finish this project?",
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C8-04", "category": "two_face", "mode": "technical", "manual": True,
        "prompt": (
            "What is the practical difference between GGUF Q4_K_M and Q4_0 quantization, "
            "and which should I use on a 12 GB VRAM GPU for a 4B parameter model?"
        ),
        "expected_tool": None, "mock_results": {},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 9 — Discord Relay Formatting
    # Must call send_discord_message with third-person, Boss-attributed message.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C9-01", "category": "discord_relay", "manual": True,
        "prompt": "Tell Farah I'll be late to the meeting.",
        "expected_tool": "send_discord_message",
        "mock_results": {"send_discord_message": "Message delivered to Farah."},
    },
    {
        "id": "C9-02", "category": "discord_relay", "manual": True,
        "prompt": "Message Tolba and ask if he's free this weekend.",
        "expected_tool": "send_discord_message",
        "mock_results": {"send_discord_message": "Message delivered to Tolba."},
    },
    {
        "id": "C9-03", "category": "discord_relay", "manual": True,
        "prompt": "Send George a thank you for helping me debug that issue.",
        "expected_tool": "send_discord_message",
        "mock_results": {"send_discord_message": "Message delivered to George."},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 10 — Multimodal / Vision
    # Populated at runtime with the captured screen. Manual accuracy review.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C10-01", "category": "vision", "vision": True, "manual": True,
        "prompt": (
            "What application is currently in focus on my screen, "
            "and what is the most prominent clickable element you can see?"
        ),
        "expected_tool": None, "mock_results": {},
    },
    {
        "id": "C10-02", "category": "vision", "vision": True, "manual": True,
        "prompt": "Is there any text visible in the center of the screen? Read it out to me word for word.",
        "expected_tool": None, "mock_results": {},
    },

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY 11 — Loop Traps (degenerate-repetition discipline)
    # Mock results are engineered to TEMPT re-calling the same tool: identical
    # unhelpful answers every time. Auto-scored by check_loop_discipline —
    # pass = ≤3 identical executions AND a non-empty honest final answer.
    # The harness runs LoopGuard exactly like production, so these measure
    # model discipline + guard behavior together.
    # ══════════════════════════════════════════════════════════════════════════

    {
        "id": "C11-01", "category": "loop_trap",
        "prompt": "What did I tell you about my cousin's startup last month?",
        "expected_tool": None,
        "mock_results": {"recall_memory": "No relevant memories found."},
    },
    {
        "id": "C11-02", "category": "loop_trap",
        "prompt": "Check my email for anything from the bank about my loan.",
        "expected_tool": None,
        "mock_results": {
            "search_emails": "No emails matched that query.",
            "summarize_unread_emails": "No unread emails in Primary.",
        },
    },
    {
        "id": "C11-03", "category": "loop_trap",
        "prompt": "Click the Submit button.",
        "expected_tool": None,
        "mock_results": {
            "smart_click": ('FAILED — could not locate "Submit" on screen. Nothing was clicked. '
                            'Try a more specific or differently-worded description, or use look_at_screen first.'),
            "look_at_screen": ("Screenshot captured. The screen shows an empty desktop with a wallpaper — "
                               "no windows, forms, or buttons are visible anywhere."),
        },
    },
    {
        "id": "C11-04", "category": "loop_trap",
        "prompt": "What song is playing right now?",
        "expected_tool": None,
        "mock_results": {"get_current_track": "No active Spotify playback found"},
    },
    {
        "id": "C11-05", "category": "loop_trap",
        "prompt": "Read my project notes file and tell me what the final deadline is.",
        "expected_tool": None,
        "mock_results": {
            "list_directory_tree": "project_notes.txt",
            "read_local_file": ("[project_notes.txt] Meeting recap: discussed sprint scope, assigned owners "
                                "for the API tasks, agreed to revisit the budget next week. "
                                "(end of file — no further content)"),
        },
    },
]
