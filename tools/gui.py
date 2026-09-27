import pyautogui


# Leave failsafe on so moving mouse to corner aborts unintended loops.
pyautogui.FAILSAFE = True


def ui_type(text: str):
    """Type text at the current cursor/focus target."""
    payload = "" if text is None else str(text)
    try:
        pyautogui.write(payload, interval=0.02)
        return "[System Note: Text input complete.]"
    except Exception as e:
        return f"Error: ui_type failed - {e}"


def ui_press_key(key: str):
    """Press a single keyboard key like enter/tab/esc."""
    payload = "" if key is None else str(key).strip().lower()
    if not payload:
        return "Error: key cannot be empty."

    try:
        pyautogui.press(payload)
        return f"[System Note: Pressed key '{payload}'.]"
    except Exception as e:
        return f"Error: ui_press_key failed - {e}"
