"""Two-Tier Dynamic & Self-Healing UI Playbook Engine for Aster.

Provides instant procedural UI memory (layout summaries, keyboard-first actions,
visual fallbacks, and anti-trap warnings) for both Desktop apps ("tier": "desktop")
and Web domains ("tier": "web"), plus on-demand learning and self-healing via
Gemini 3.8 Flash.
"""
import base64
import datetime
import json
import os
import re
from typing import Any

import config


def _playbooks_dir() -> str:
    """Resolve the playbooks directory from config (with fallback to Aster_Vault/playbooks)."""
    return getattr(config, "PLAYBOOKS_DIR", os.path.join(config.VAULT_DIR, "playbooks"))


def _slugify(value: str) -> str:
    """Normalize an app/site identifier into a safe filename slug."""
    s = (value or "").strip().lower()
    s = re.sub(r"\.json$", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "unknown"


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _bump_version(ver: str) -> str:
    """Increment the minor version string (e.g. '1.0' -> '1.1')."""
    try:
        parts = str(ver or "1.0").split(".")
        if len(parts) >= 2:
            major = int(parts[0])
            minor = int(parts[1]) + 1
            return f"{major}.{minor}"
        return f"{int(parts[0])}.1"
    except Exception:
        return "1.1"


def save_playbook(playbook: dict[str, Any]) -> str:
    """Persist a playbook dict to `<PLAYBOOKS_DIR>/<id>.json` and return its path."""
    pb_dir = _playbooks_dir()
    os.makedirs(pb_dir, exist_ok=True)
    pb_id = _slugify(str(playbook.get("id") or playbook.get("name") or "unknown"))
    playbook["id"] = pb_id
    path = os.path.join(pb_dir, f"{pb_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(playbook, f, indent=2, ensure_ascii=False)
    return path


def load_playbook(playbook_id: str) -> dict[str, Any] | None:
    """Load `<playbook_id>.json` from PLAYBOOKS_DIR (normalizing slug)."""
    if not playbook_id:
        return None
    pb_dir = _playbooks_dir()
    slug = _slugify(playbook_id)
    candidates = [slug]
    if not slug.startswith("web_"):
        candidates.append(f"web_{slug}")

    for cand in candidates:
        path = os.path.join(pb_dir, f"{cand}.json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                print(f"[Playbooks: Failed to read {path}: {e}]")
    return None


def list_playbooks() -> list[dict[str, Any]]:
    """Load all `.json` playbooks in PLAYBOOKS_DIR."""
    pb_dir = _playbooks_dir()
    if not os.path.isdir(pb_dir):
        return []
    out: list[dict[str, Any]] = []
    for fname in sorted(os.listdir(pb_dir)):
        if not fname.lower().endswith(".json"):
            continue
        path = os.path.join(pb_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
        except Exception as e:
            print(f"[Playbooks: Skipping invalid JSON {fname}: {e}]")
    return out


def _strip_browser_suffix(text_lower: str) -> str:
    """Strip trailing browser app names (e.g. ' - google chrome') when scoring web playbooks."""
    out = re.sub(
        r"\s*[-—|]\s*(?:google chrome|microsoft\u200b?\s*edge|brave|mozilla firefox)\s*$",
        "",
        text_lower,
    )
    return out


def _score_playbook_match(pb: dict[str, Any], text_lower: str, query_slug: str = "") -> int:
    """Return a positive match score if `pb` matches `text_lower`, else 0."""
    if not text_lower and not query_slug:
        return 0

    pb_id = str(pb.get("id", "")).lower()
    pb_id_stripped = pb_id[4:] if pb_id.startswith("web_") else pb_id
    pb_name = str(pb.get("name", "")).lower()
    tier = str(pb.get("tier", "desktop")).lower()
    match_cfg = pb.get("match") or {}
    title_keywords = [str(k).lower() for k in (match_cfg.get("title_keywords") or []) if k]
    domains = [str(d).lower() for d in (match_cfg.get("domains") or []) if d]
    process_names = [str(p).lower() for p in (match_cfg.get("process_names") or []) if p]

    # Exact ID / slug / name hit when looking up by app_or_site
    if query_slug and query_slug in (pb_id, pb_id_stripped, _slugify(pb_name)):
        return 100

    # For web playbooks, do not let the host browser suffix (' - Google Chrome')
    # masquerade as a Google Search page title (' - google ').
    effective_text = _strip_browser_suffix(text_lower) if tier == "web" else text_lower
    if not effective_text:
        return 0

    best = 0
    for dom in domains:
        if dom and dom in effective_text:
            best = max(best, 90 + len(dom))

    for kw in title_keywords:
        if kw and kw in effective_text:
            best = max(best, 80 + len(kw))

    for proc in process_names:
        proc_stem = proc[:-4] if proc.endswith(".exe") else proc
        if proc in effective_text or (len(proc_stem) >= 4 and proc_stem in effective_text):
            best = max(best, 75 + len(proc_stem))

    if pb_name and pb_name in effective_text:
        best = max(best, 70 + len(pb_name))

    if pb_id_stripped and len(pb_id_stripped) >= 4 and pb_id_stripped in effective_text:
        # Avoid matching 'google' inside 'google chrome' for web_google
        if pb_id == "web_google" and "google chrome" in text_lower and "google" not in effective_text:
            pass
        else:
            best = max(best, 60 + len(pb_id_stripped))

    return best


def resolve_active_playbook(
    window_title: str = "",
    screen_description: str = "",
) -> dict[str, Any] | None:
    """Resolve the active UI playbook with Two-Tier priority (web domain > desktop app).

    Checks `window_title` first (strongest ground truth), then `screen_description`.
    Within each text source:
      1. Check `"tier": "web"` playbooks first so e.g. `"Lo-Fi Beats - YouTube - Google Chrome"`
         resolves to `web_youtube` rather than `chrome`.
      2. Check `"tier": "desktop"` playbooks next.
    """
    playbooks = list_playbooks()
    if not playbooks:
        return None

    web_pbs = [p for p in playbooks if str(p.get("tier", "")).lower() == "web"]
    desktop_pbs = [p for p in playbooks if str(p.get("tier", "desktop")).lower() != "web"]

    title_lower = (window_title or "").strip().lower()
    desc_lower = (screen_description or "").strip().lower()
    title_slug = _slugify(window_title) if window_title else ""

    # Pass 1: Match against window_title (Web tier first, then Desktop tier)
    if title_lower:
        best_web = max(
            ((pb, _score_playbook_match(pb, title_lower, title_slug)) for pb in web_pbs),
            key=lambda item: item[1],
            default=(None, 0),
        )
        if best_web[0] is not None and best_web[1] > 0:
            return best_web[0]

        best_desk = max(
            ((pb, _score_playbook_match(pb, title_lower, title_slug)) for pb in desktop_pbs),
            key=lambda item: item[1],
            default=(None, 0),
        )
        if best_desk[0] is not None and best_desk[1] > 0:
            return best_desk[0]

    # Pass 2: Match against screen_description (Web tier first, then Desktop tier)
    if desc_lower:
        best_web = max(
            ((pb, _score_playbook_match(pb, desc_lower)) for pb in web_pbs),
            key=lambda item: item[1],
            default=(None, 0),
        )
        if best_web[0] is not None and best_web[1] > 0:
            return best_web[0]

        best_desk = max(
            ((pb, _score_playbook_match(pb, desc_lower)) for pb in desktop_pbs),
            key=lambda item: item[1],
            default=(None, 0),
        )
        if best_desk[0] is not None and best_desk[1] > 0:
            return best_desk[0]

    return None


def _format_action_line(action_name: str, spec: dict[str, Any]) -> str:
    """Format a single action entry compactly for hint/tool output."""
    primary = spec.get("primary") or spec.get("shortcut") or ""
    fallback = spec.get("visual_fallback") or ""
    if fallback:
        return f"{action_name}: {primary} (fallback: {fallback})"
    return f"{action_name}: {primary}"


def get_active_playbook_hint(
    window_title: str = "",
    screen_description: str = "",
) -> str:
    """Return a compact (~5-8 line) UI playbook hint for injection into vision/motor turns."""
    if not window_title:
        try:
            from tools.uia import get_foreground_window_title

            window_title = get_foreground_window_title()
        except Exception:
            window_title = ""

    pb = resolve_active_playbook(window_title=window_title, screen_description=screen_description)
    if pb is not None:
        name = pb.get("name") or pb.get("id") or "Unknown"
        pb_id = pb.get("id") or _slugify(name)
        tier = pb.get("tier") or "desktop"
        lines = [f"[UI Playbook: {name} ({pb_id}) | Tier: {tier}]"]

        layout = (pb.get("layout_summary") or "").strip()
        if layout:
            lines.append(f"Layout: {layout}")

        actions = pb.get("actions") or {}
        if isinstance(actions, dict) and actions:
            formatted_actions = []
            for act_name, spec in list(actions.items())[:8]:
                if isinstance(spec, dict):
                    formatted_actions.append(_format_action_line(act_name, spec))
            if formatted_actions:
                lines.append("Actions: " + " | ".join(formatted_actions))

        if str(tier).lower() == "web":
            lines.append(
                "Browser Host Shortcuts: new_tab=press_key('ctrl+t'), "
                "focus_address_bar=press_key('ctrl+l'), close_tab=press_key('ctrl+w'), "
                "switch_tab=press_key('ctrl+tab')"
            )

        traps = [str(t).strip() for t in (pb.get("avoid_traps") or []) if str(t).strip()]
        if traps:
            lines.append(f"AVOID: {'; '.join(traps)}")

        return "\n".join(lines)

    if window_title:
        return (
            f"[UI Playbook: None cached for {window_title!r}. "
            f"If unsure how to navigate this UI, call get_ui_playbook(app_or_site={window_title!r}, task='...').]"
        )
    return ""


def format_full_playbook(pb: dict[str, Any], header_prefix: str = "") -> str:
    """Format a complete playbook dictionary for the `get_ui_playbook` tool response."""
    name = pb.get("name") or pb.get("id") or "Unknown"
    pb_id = pb.get("id") or _slugify(name)
    tier = pb.get("tier") or "desktop"
    meta = pb.get("metadata") or {}
    ver = meta.get("version", "1.0")
    source = meta.get("source", "preseed")

    lines: list[str] = []
    if header_prefix:
        lines.append(header_prefix)
    lines.append(f"[UI Playbook: {name} ({pb_id}) | Tier: {tier} | v{ver} ({source})]")

    layout = (pb.get("layout_summary") or "").strip()
    if layout:
        lines.append(f"Layout: {layout}")

    actions = pb.get("actions") or {}
    if isinstance(actions, dict) and actions:
        lines.append("Actions:")
        for act_name, spec in actions.items():
            if isinstance(spec, dict):
                line = f"  - {_format_action_line(act_name, spec)}"
                verify = spec.get("verify")
                if verify:
                    line += f" -> verify: {verify}"
                lines.append(line)

    if str(tier).lower() == "web":
        lines.append(
            "Browser Host Shortcuts: new_tab=press_key('ctrl+t'), "
            "focus_address_bar=press_key('ctrl+l'), close_tab=press_key('ctrl+w'), "
            "switch_tab=press_key('ctrl+tab')"
        )

    traps = [str(t).strip() for t in (pb.get("avoid_traps") or []) if str(t).strip()]
    if traps:
        lines.append(f"AVOID: {'; '.join(traps)}")

    return "\n".join(lines)


def _task_matches_existing_actions(task: str, actions: dict[str, Any]) -> bool:
    """Check whether `task` is already covered by an existing action in `actions`."""
    if not task or not task.strip():
        return True
    if not isinstance(actions, dict) or not actions:
        return False

    task_lower = task.strip().lower()
    task_slug = _slugify(task_lower)
    if task_slug in actions:
        return True

    stop_words = {
        "the", "a", "an", "to", "in", "on", "of", "for", "and", "or",
        "how", "do", "i", "please", "can", "you", "with", "from", "into",
    }
    tokens = [w for w in re.findall(r"[a-z0-9]+", task_lower) if w not in stop_words and len(w) >= 3]
    if not tokens:
        return True

    for act_name, spec in actions.items():
        act_text = f"{act_name} "
        if isinstance(spec, dict):
            act_text += " ".join(str(v) for v in spec.values() if v)
        act_text_lower = act_text.lower()
        if any(tok in act_text_lower for tok in tokens):
            return True

    return False


def _query_gemini_playbook(
    app_or_site: str,
    task: str = "",
    failed_step: str = "",
    existing_playbook: dict[str, Any] | None = None,
    screenshot_b64: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Query Gemini 3.8 Flash to learn, enrich, or self-heal a UI playbook JSON."""
    api_key = getattr(config, "GEMINI_API_KEY", "") or config._secret("gemini", "api_key", default="")
    model_name = getattr(config, "GEMINI_MODEL", "gemini-3.8-flash") or "gemini-3.8-flash"
    if not api_key:
        return None, "Gemini API key not configured in secrets.yaml"

    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        return None, f"google-genai SDK not available: {e}"

    schema_instruction = (
        "You are a Windows 11 Desktop & Web UI Playbook Architect for an autonomous agent "
        "whose motor tools are:\n"
        "- press_key('shortcut') (e.g. 'ctrl+t', 'ctrl+k', 'enter', 'escape', '/')\n"
        "- type_text('text')\n"
        "- smart_click('concise visual label or description')\n"
        "- smart_type('concise input field description', 'text', submit=true)\n"
        "- scroll_screen('down' or 'up', clicks)\n\n"
        "Return ONLY a single valid JSON object matching this exact schema:\n"
        "{\n"
        '  "id": "slug_id",\n'
        '  "tier": "desktop" or "web",\n'
        '  "name": "Human Readable Name",\n'
        '  "match": {\n'
        '    "process_names": ["app.exe"],\n'
        '    "title_keywords": ["keyword"],\n'
        '    "domains": ["example.com"]\n'
        "  },\n"
        '  "layout_summary": "Concise 1-2 sentence description of spatial layout.",\n'
        '  "avoid_traps": ["Specific visual trap or wrong button to avoid..."],\n'
        '  "actions": {\n'
        '    "action_slug": {\n'
        '      "shortcut": "keyboard shortcut or empty string",\n'
        '      "primary": "keyboard-first or most reliable step sequence",\n'
        '      "visual_fallback": "smart_click or smart_type fallback",\n'
        '      "verify": "What changes on screen when successful"\n'
        "    }\n"
        "  }\n"
        "}\n"
        "Always prefer keyboard shortcuts for `primary` where reliable, and concrete "
        "`smart_click`/`smart_type` descriptions for `visual_fallback`."
    )

    context_lines = [schema_instruction, f"\nTarget Application or Website: {app_or_site}"]
    if task:
        context_lines.append(f"Current User Task / Goal: {task}")
    if failed_step:
        context_lines.append(
            f"FAILED STEP TO SELF-HEAL: {failed_step}\n"
            "Analyze why this step failed (using the attached screenshot if provided), "
            "add a warning to `avoid_traps` so the agent does not repeat the mistake, "
            "and update `actions` and `layout_summary` with the corrected navigation procedure."
        )
    if existing_playbook:
        context_lines.append(
            "Existing Playbook JSON (preserve working actions while adding/fixing the requested task):\n"
            + json.dumps(existing_playbook, indent=2)
        )

    prompt_text = "\n".join(context_lines)

    try:
        client = genai.Client(api_key=api_key)
        contents: list[Any] = []
        if screenshot_b64:
            try:
                img_bytes = base64.b64decode(screenshot_b64)
                contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))
            except Exception:
                pass
        contents.append(prompt_text)

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        raw_text = (getattr(response, "text", None) or "").strip()
        if not raw_text:
            return None, "Gemini returned an empty response"

        # Strip accidental markdown code fences if any
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()

        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            return None, "Gemini response was not a JSON object"
        return parsed, ""
    except Exception as e:
        return None, f"Gemini playbook query failed: {e}"


def _merge_or_normalize_playbook(
    generated: dict[str, Any],
    app_or_site: str,
    existing: dict[str, Any] | None,
    source: str,
) -> dict[str, Any]:
    """Normalize a Gemini-returned playbook and merge metadata cleanly."""
    today = _today_iso()
    base = existing.copy() if isinstance(existing, dict) else {}

    pb_id = (
        base.get("id")
        or generated.get("id")
        or _slugify(app_or_site)
    )
    pb_id = _slugify(str(pb_id))

    tier = generated.get("tier") or base.get("tier") or (
        "web" if "." in app_or_site and not app_or_site.lower().endswith(".exe") else "desktop"
    )
    name = generated.get("name") or base.get("name") or app_or_site.strip()

    old_match = base.get("match") or {}
    gen_match = generated.get("match") or {}
    match_block = {
        "process_names": list(dict.fromkeys((old_match.get("process_names") or []) + (gen_match.get("process_names") or []))),
        "title_keywords": list(dict.fromkeys((old_match.get("title_keywords") or []) + (gen_match.get("title_keywords") or []))),
        "domains": list(dict.fromkeys((old_match.get("domains") or []) + (gen_match.get("domains") or []))),
    }
    if not match_block["title_keywords"] and name:
        match_block["title_keywords"] = [name.lower()]

    old_meta = base.get("metadata") or {}
    version = _bump_version(old_meta.get("version", "1.0")) if existing else str(old_meta.get("version", "1.0"))
    metadata = {
        "version": version,
        "source": source,
        "created_at": old_meta.get("created_at") or today,
        "last_verified": today,
        "consecutive_failures": 0,
        "usage_count": int(old_meta.get("usage_count", 0)) + 1,
    }

    layout_summary = generated.get("layout_summary") or base.get("layout_summary") or ""
    old_traps = base.get("avoid_traps") or []
    gen_traps = generated.get("avoid_traps") or []
    avoid_traps = list(dict.fromkeys([str(t) for t in old_traps + gen_traps if t]))

    merged_actions: dict[str, Any] = {}
    if isinstance(base.get("actions"), dict):
        merged_actions.update(base["actions"])
    if isinstance(generated.get("actions"), dict):
        merged_actions.update(generated["actions"])

    return {
        "id": pb_id,
        "tier": tier,
        "name": name,
        "match": match_block,
        "metadata": metadata,
        "layout_summary": layout_summary,
        "avoid_traps": avoid_traps,
        "actions": merged_actions,
    }


def get_ui_playbook(app_or_site: str, task: str = "", failed_step: str = "") -> str:
    """Retrieve, enrich, or self-heal the UI navigation playbook for `app_or_site`.

    - Mode A (Self-Healing): `failed_step` is non-empty -> captures current screen,
      queries Gemini 3.8 Flash with the failure context + screenshot, updates the cached
      playbook on disk, and returns the healed playbook.
    - Mode B (Local Hit): `failed_step` is empty and playbook exists locally -> returns
      immediately (or enriches via Gemini 3.8 Flash if `task` is not yet in `actions`).
    - Mode C (Dynamic Learning): Playbook does not exist locally -> queries Gemini 3.8
      Flash to synthesize and cache a new playbook in `Aster_Vault/playbooks/<slug>.json`.
    """
    target = (app_or_site or "").strip()
    if not target:
        return "Error: `app_or_site` is required."

    existing = load_playbook(target) or resolve_active_playbook(window_title=target)

    # ------------------------------------------------------------------
    # Mode A: Self-Healing (`failed_step` is non-empty)
    # ------------------------------------------------------------------
    if failed_step and failed_step.strip():
        if existing is not None:
            meta = existing.setdefault("metadata", {})
            meta["consecutive_failures"] = int(meta.get("consecutive_failures", 0)) + 1
            try:
                save_playbook(existing)
            except Exception:
                pass

        screenshot_b64 = None
        try:
            from tools.vision import capture_screen_base64

            screenshot_b64 = capture_screen_base64()
        except Exception:
            screenshot_b64 = None

        healed_raw, err = _query_gemini_playbook(
            app_or_site=target,
            task=task,
            failed_step=failed_step,
            existing_playbook=existing,
            screenshot_b64=screenshot_b64,
        )
        if healed_raw is not None:
            healed = _merge_or_normalize_playbook(
                healed_raw,
                app_or_site=target,
                existing=existing,
                source="self-healed",
            )
            save_playbook(healed)
            return format_full_playbook(healed, header_prefix="[UI Playbook Self-Healed via Gemini 3.8 Flash]")

        if existing is not None:
            return (
                f"[UI Playbook Self-Heal Fallback — {err}]\n"
                + format_full_playbook(existing)
            )
        return f"[UI Playbook: Could not self-heal playbook for {target!r} — {err}]"

    # ------------------------------------------------------------------
    # Mode B: Local Hit (`failed_step` is empty, and playbook exists)
    # ------------------------------------------------------------------
    if existing is not None:
        actions = existing.get("actions") or {}
        if task and not _task_matches_existing_actions(task, actions):
            enriched_raw, _err = _query_gemini_playbook(
                app_or_site=target,
                task=task,
                existing_playbook=existing,
            )
            if enriched_raw is not None:
                enriched = _merge_or_normalize_playbook(
                    enriched_raw,
                    app_or_site=target,
                    existing=existing,
                    source="gemini-3.8-flash",
                )
                save_playbook(enriched)
                return format_full_playbook(
                    enriched,
                    header_prefix="[UI Playbook Enriched via Gemini 3.8 Flash]",
                )

        meta = existing.setdefault("metadata", {})
        meta["usage_count"] = int(meta.get("usage_count", 0)) + 1
        try:
            save_playbook(existing)
        except Exception:
            pass
        return format_full_playbook(existing)

    # ------------------------------------------------------------------
    # Mode C: Dynamic Learning via Gemini 3.8 Flash (no local playbook)
    # ------------------------------------------------------------------
    learned_raw, err = _query_gemini_playbook(app_or_site=target, task=task)
    if learned_raw is not None:
        learned = _merge_or_normalize_playbook(
            learned_raw,
            app_or_site=target,
            existing=None,
            source="gemini-3.8-flash",
        )
        save_playbook(learned)
        return format_full_playbook(
            learned,
            header_prefix="[UI Playbook Learned & Cached via Gemini 3.8 Flash]",
        )

    return (
        f"[UI Playbook: No cached playbook for {target!r} and dynamic learning was unavailable ({err}). "
        "Proceed using look_at_screen(), keyboard shortcuts, or smart_click/smart_type.]"
    )


def record_playbook_outcome(app_or_site: str, success: bool) -> bool:
    """Update `last_verified` and `consecutive_failures` on a playbook after use."""
    pb = load_playbook(app_or_site) or resolve_active_playbook(window_title=app_or_site)
    if pb is None:
        return False
    meta = pb.setdefault("metadata", {})
    if success:
        meta["last_verified"] = _today_iso()
        meta["consecutive_failures"] = 0
    else:
        meta["consecutive_failures"] = int(meta.get("consecutive_failures", 0)) + 1
    try:
        save_playbook(pb)
        return True
    except Exception:
        return False


def get_stale_or_failing_playbooks(
    max_age_days: int = 90,
    min_failures: int = 2,
) -> list[dict[str, Any]]:
    """Return all cached playbooks whose age >= `max_age_days` or `consecutive_failures` >= `min_failures`."""
    today = datetime.date.today()
    flagged: list[dict[str, Any]] = []
    for pb in list_playbooks():
        meta = pb.get("metadata") or {}
        failures = int(meta.get("consecutive_failures", 0) or 0)
        if failures >= min_failures:
            flagged.append(pb)
            continue

        last_ver_str = str(meta.get("last_verified") or meta.get("created_at") or "").strip()
        if not last_ver_str:
            flagged.append(pb)
            continue
        try:
            last_ver_date = datetime.date.fromisoformat(last_ver_str[:10])
            if (today - last_ver_date).days >= max_age_days:
                flagged.append(pb)
        except Exception:
            flagged.append(pb)

    return flagged
