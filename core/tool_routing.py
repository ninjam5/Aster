"""Tool tie-break (ID 8 of laya-integration.md).

Several ADMIN_TOOLS descriptions overlap, so the 35B model has to disambiguate between
near-duplicate schemas — a known source of harness `single_tool` flakiness (`C1-06`).
This module gives Laya a bounded pick within one overlapping cluster, and the brain
injects the result as a one-line hint on the first round.

Design notes:
  - A cheap regex pre-filter runs first, so ordinary chat costs NO model call.
  - The hint is appended to the END of the eval messages (like the Discord relay
    directive), never into the system prompt — the cached prefix is untouched.
  - It is a HINT, not an override: the model still chooses. Low margin, an unrecognized
    verdict or any failure simply means no hint (today's behaviour).
"""
import re

# Each cluster: the overlapping tools and the distinguishing clause for each.
CLUSTERS = {
    "read_web": {
        "trigger": re.compile(r"https?://|www\.|\blink\b|\burl\b|\bweb ?page\b|\barticle\b",
                              re.IGNORECASE),
        "criteria": {
            "research": ("look up facts, or read ONE page through the web API — no browser, "
                         "works when the browser is unavailable or RAM is low"),
            "browse_web": ("drive a real browser and interact with a page — clicking, typing, "
                           "logging in, or reading what is actually on screen"),
        },
    },
    "remember": {
        "trigger": re.compile(r"\bremember\b|\bnote\b|\bmemor\w*\b|\bkeep (?:this|that) in mind\b|"
                              r"\bdon'?t forget\b", re.IGNORECASE),
        "criteria": {
            "save_note": "store a short user-authored note or to-do list",
            "memorize_fact": "store a durable fact about the owner in long-term memory",
        },
    },
    "type": {
        "trigger": re.compile(r"\btype\b|\bfill (?:in|out)\b|\benter\b|\bsearch (?:for|bar)\b",
                              re.IGNORECASE),
        "criteria": {
            "smart_type": "type into a named field/element found by description (a search bar, a form input)",
            "type_text": "type text at the current cursor position in whatever has focus",
        },
    },
    "screen": {
        "trigger": re.compile(r"\bwhat'?s on my screen\b|\blook at (?:my )?screen\b|"
                              r"\bhighlight\b|\bshow me where\b|\bpoint (?:me )?(?:to|at)\b",
                              re.IGNORECASE),
        "criteria": {
            "look_at_screen": "describe what is currently on the screen",
            "highlight_on_screen": "draw a visual highlight on a specific element the user asked about",
        },
    },
}


def pick_tool_for_request(user_text: str) -> dict:
    """Which tool wins in an overlapping cluster for this request? (ID 8)

    Returns {"cluster", "tool", "escalate", "margin", "reason"}. `tool` is None when no
    cluster matches, the kernel is off, the pick is uncertain, or anything fails — in
    every one of those cases the caller behaves exactly as before (no hint).
    """
    none = {"cluster": None, "tool": None, "escalate": True, "margin": None, "reason": ""}
    text = str(user_text or "")
    if not text.strip():
        return none

    cluster_name, cluster = None, None
    for name, spec in CLUSTERS.items():
        if spec["trigger"].search(text):
            cluster_name, cluster = name, spec
            break
    if cluster is None:
        return {**none, "reason": "no cluster trigger"}

    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return {**none, "cluster": cluster_name, "reason": "kernel disabled"}
        criteria = dict(cluster["criteria"])
        criteria["Z"] = "none of these — the request does not need one of them"
        verdict = system1.choose(
            "Which tool should handle this request?",
            criteria, key="tool_pick", state={"request": text[:400]}, min_margin=0.5)
    except Exception as e:
        return {**none, "cluster": cluster_name, "reason": f"kernel error ({e.__class__.__name__})"}

    if verdict.get("escalate"):
        return {**none, "cluster": cluster_name,
                "reason": verdict.get("reason") or "low or unavailable margin"}
    choice = verdict.get("choice")
    if not choice or choice == "Z":
        return {**none, "cluster": cluster_name, "reason": "no tool picked"}
    return {"cluster": cluster_name, "tool": choice, "escalate": False,
            "margin": verdict.get("margin"), "reason": "Laya pick"}


def hint_for_tool(tool: str) -> str:
    """The one-line hint the brain appends for a confidently picked tool."""
    for spec in CLUSTERS.values():
        if tool in spec["criteria"]:
            return (f"[System note: for this request prefer `{tool}` — "
                    f"{spec['criteria'][tool]}.]")
    return ""
