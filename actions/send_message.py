import json
import io
import platform
import subprocess
import sys
import time
from pathlib import Path

def _enable_windows_dpi_awareness() -> str:
    """Set DPI awareness before this module imports PyAutoGUI."""
    if platform.system() != "Windows":
        return "not-applicable"
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor aware"
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return "system aware (fallback)"
        except Exception:
            return "unavailable"


_DPI_AWARENESS = _enable_windows_dpi_awareness()

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.06
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", "windows").lower()
    except Exception:
        return "windows"


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

def _open_app(app_name: str) -> bool:
    _require_pyautogui()
    os_name = _get_os()

    try:
        if os_name == "windows":
            pyautogui.press("win")
            time.sleep(0.5)
            _paste_text(app_name)
            time.sleep(0.6)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True

        elif os_name == "mac":
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                result = subprocess.run(
                    ["open", "-a", f"{app_name}.app"],
                    capture_output=True, text=True, timeout=10,
                )
            time.sleep(2.5)
            return result.returncode == 0

        else: 
            launched = False
            for launcher in [
                ["gtk-launch", app_name.lower()],
                [app_name.lower()],
            ]:
                try:
                    subprocess.Popen(
                        launcher,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            time.sleep(2.5)
            return launched

    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open {app_name}: {e}")
        return False


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        # Ask the platform handler to reuse the existing browser session.
        webbrowser.open(url, new=0)
        time.sleep(4.0) 
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False


def _messenger_escalation_url(url: str | None = None) -> str:
    if url and str(url).strip():
        return str(url).strip()
    from core.reminder_escalation import get_messenger_escalation_url
    return get_messenger_escalation_url()


def _search_in_app(query: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")

    pyautogui.hotkey(*search_hotkey)
    time.sleep(0.5)
    _clear_and_paste(query)
    time.sleep(1.0)

def _desktop_send(app_name: str, receiver: str, message: str) -> str:
    if not _open_app(app_name):
        return f"Could not open {app_name}."

    time.sleep(1.0)
    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)
    return f"Message sent to {receiver} via {app_name}."

def _send_whatsapp(receiver: str, message: str) -> str:
    return _desktop_send("WhatsApp", receiver, message)

def _screen_verdict(prompt: str, allowed: tuple[str, ...]) -> str | None:
    """Use the existing Gemini vision helper for a strict UI verdict."""
    try:
        from google.genai import types as gtypes
        from core import gemini

        image = pyautogui.screenshot()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        response = gemini.call(
            [
                gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"),
                prompt + "\nReply with exactly one of: " + ", ".join(allowed),
            ],
            tier=gemini.FAST,
            timeout_ms=20_000,
        )
        if response is None:
            return None
        answer = (response.text or "").strip().upper()
        for value in allowed:
            if answer.startswith(value.upper()):
                return value
    except Exception as e:
        print(f"[SendMessage] ⚠️ UI verification unavailable: {e}")
    return None


def _active_window_dimensions() -> str:
    """Return best-effort geometry for the active desktop window."""
    try:
        import pygetwindow

        window = pygetwindow.getActiveWindow()
        if window is not None:
            return (
                f"{int(window.width)}x{int(window.height)} "
                f"at ({int(window.left)},{int(window.top)})"
            )
    except Exception:
        pass
    return "unavailable"


def _windows_dpi_scaling() -> str:
    if platform.system() != "Windows":
        return "not-applicable"
    try:
        import ctypes

        user32 = ctypes.windll.user32
        system_dpi = int(user32.GetDpiForSystem()) if hasattr(user32, "GetDpiForSystem") else 96
        return f"{system_dpi} DPI ({system_dpi / 96:.0%})"
    except Exception:
        return "unavailable"


def _focus_existing_browser() -> bool:
    """Bring an existing browser window to the foreground when discoverable."""
    browser_terms = (
        "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "browser",
        "messenger", "facebook",
    )
    try:
        import pygetwindow

        windows = list(pygetwindow.getAllWindows())
        candidates = [
            window for window in windows
            if str(getattr(window, "title", "")).strip()
            and any(term in str(window.title).lower() for term in browser_terms)
        ]
        if not candidates:
            return False
        window = candidates[0]
        try:
            if getattr(window, "isMinimized", False):
                window.restore()
        except Exception:
            pass
        window.activate()
        time.sleep(0.5)
        active = pygetwindow.getActiveWindow()
        return active is not None and any(
            term in str(getattr(active, "title", "")).lower()
            for term in browser_terms
        )
    except Exception as exc:
        print(f"[SendMessage] Browser focus unavailable: {exc}")
        return False


def _browser_window_geometry() -> dict | None:
    """Return active-window geometry for fullscreen verification."""
    try:
        import pygetwindow

        window = pygetwindow.getActiveWindow()
        if window is None:
            return None
        return {
            "title": str(getattr(window, "title", "")),
            "left": int(window.left),
            "top": int(window.top),
            "width": int(window.width),
            "height": int(window.height),
        }
    except Exception:
        return None


def _enter_messenger_fullscreen() -> tuple[bool, dict]:
    """Enter browser F11 fullscreen and verify the coordinate space."""
    _require_pyautogui()
    if not _focus_existing_browser():
        return False, {"reason": "Existing browser window could not be focused."}

    screen_size = tuple(int(value) for value in pyautogui.size())
    before = _browser_window_geometry()
    print(f"MESSENGER_READY browser_geometry={before or 'unavailable'}")
    already_fullscreen = bool(
        before
        and before["left"] <= 0
        and before["top"] <= 0
        and before["width"] >= screen_size[0] - 2
        and before["height"] >= screen_size[1] - 2
    )
    if not already_fullscreen:
        pyautogui.press("f11")
        time.sleep(1.2)
    else:
        print("MESSENGER_FULLSCREEN already_active=true")
    after = _browser_window_geometry()
    geometry_ok = bool(
        after
        and after["left"] <= 0
        and after["top"] <= 0
        and after["width"] >= screen_size[0] - 2
        and after["height"] >= screen_size[1] - 2
    )
    diagnostics = {
        "screen_size": screen_size,
        "browser_window_before": before,
        "browser_window_after": after,
        "fullscreen_active": geometry_ok,
        "dpi_scaling": _windows_dpi_scaling(),
    }
    print(
        "MESSENGER_FULLSCREEN "
        f"active={geometry_ok} screen={screen_size} "
        f"browser_geometry={after or 'unavailable'} "
        f"dpi={diagnostics['dpi_scaling']}"
    )
    if not geometry_ok:
        diagnostics["reason"] = (
            "F11 did not produce a verified fullscreen browser window."
        )
    return geometry_ok, diagnostics


def _find_messenger_call_button(
    diagnostic: bool = False,
) -> tuple[tuple[int, int] | None, dict]:
    """Find the phone icon in the loaded Messenger conversation header."""
    from actions.computer_control import _screen_find

    result = _screen_find(
        "the phone handset voice-call button in the Messenger conversation "
        "header. It must be the audio/voice call control for the open DM. "
        "Do not select the video camera button, search button, conversation "
        "options button, back button, profile button, or any browser control.",
        return_diagnostics=diagnostic,
        require_direct_coordinates=True,
    )
    if diagnostic and isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        coords, metadata = result
    else:
        coords, metadata = result, {}

    if diagnostic:
        print("CALL BUTTON DETECTION")
        print(f"Physical screen size: {metadata.get('physical_screen_size', 'unavailable')}")
        print(f"PyAutoGUI screen size: {metadata.get('pyautogui_screen_size', 'unavailable')}")
        print(f"Screenshot size: {metadata.get('screenshot_size', 'unavailable')}")
        print(f"Messenger window position/size: {_active_window_dimensions()}")
        print(f"Screenshot crop/offset: {metadata.get('screenshot_crop_offset', 'unavailable')}")
        print(f"Detected button X/Y: {metadata.get('raw_coordinates', 'NOT_FOUND')}")
        print(f"Final PyAutoGUI click X/Y: {metadata.get('final_coordinates', 'NOT_FOUND')}")
        print(f"Coordinate transform: {metadata.get('coordinate_transform', 'unavailable')}")
        print(f"Windows DPI scaling: {_windows_dpi_scaling()}")
        if not coords:
            print("Messenger voice-call button could not be visually identified")
    return coords, metadata


def send_whatsapp_verified(receiver: str, message: str) -> dict:
    """Send through native WhatsApp and require visual confirmation."""
    _require_pyautogui()
    if _get_os() != "windows":
        return {"sent": False, "detail": "Native Windows WhatsApp is required."}
    try:
        if not _open_app("WhatsApp"):
            return {"sent": False, "detail": "Could not open native WhatsApp."}
        time.sleep(1.0)
        _search_in_app(receiver)
        pyautogui.press("enter")
        time.sleep(0.8)
        contact_verdict = _screen_verdict(
            f"Is native WhatsApp showing the exact contact '{receiver}'? "
            "Use UNKNOWN unless clearly visible.",
            ("YES", "NO", "UNKNOWN"),
        )
        if contact_verdict != "YES":
            return {
                "sent": False,
                "detail": f"Exact WhatsApp contact was not verified ({contact_verdict or 'UNKNOWN'}).",
            }
        _paste_text(message)
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(1.0)
        sent_verdict = _screen_verdict(
            f"Was this exact message visibly sent in the native WhatsApp "
            f"conversation with '{receiver}': {message!r}? Require an outgoing "
            "bubble and sent/delivered indication; use UNKNOWN if unclear.",
            ("YES", "NO", "UNKNOWN"),
        )
        if sent_verdict == "YES":
            return {"sent": True, "detail": f"WhatsApp confirmed delivery to {receiver}."}
        return {
            "sent": False,
            "detail": f"WhatsApp delivery was not verified ({sent_verdict or 'UNKNOWN'}).",
        }
    except Exception as e:
        return {"sent": False, "detail": f"WhatsApp escalation failed safely: {e}"}


def verify_whatsapp_delivery(receiver: str, message: str) -> dict:
    """Verify an already-attempted WhatsApp send without sending again."""
    _require_pyautogui()
    if _get_os() != "windows":
        return {"sent": False, "detail": "Native Windows WhatsApp is required."}
    try:
        if not _open_app("WhatsApp"):
            return {"sent": None, "detail": "Could not open WhatsApp for recovery verification."}
        time.sleep(1.0)
        _search_in_app(receiver)
        pyautogui.press("enter")
        time.sleep(0.8)
        verdict = _screen_verdict(
            f"Is native WhatsApp showing the exact contact '{receiver}' with "
            f"this outgoing message visibly sent and delivered: {message!r}? "
            "Do not infer success; use UNKNOWN unless clearly visible.",
            ("YES", "NO", "UNKNOWN"),
        )
        if verdict == "YES":
            return {"sent": True, "detail": f"WhatsApp recovery verified delivery to {receiver}."}
        return {
            "sent": False,
            "detail": f"WhatsApp recovery could not verify delivery ({verdict or 'UNKNOWN'}).",
        }
    except Exception as e:
        return {"sent": None, "detail": f"WhatsApp recovery verification failed safely: {e}"}


def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("Telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("Signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("Discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram in browser."

    _paste_text(receiver)
    time.sleep(1.5)

    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")   
    time.sleep(0.4)

    for _ in range(4):
        pyautogui.press("tab")
        time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(2.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Instagram."


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return "Could not open Messenger in browser."


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Messenger."


def start_messenger_call(
    receiver: str,
    max_duration_minutes: int = 5,
    conversation_url: str | None = None,
) -> str:
    """Open a Messenger conversation and attempt its audio-call control.

    Messenger's logged-in browser session and UI vary by account and browser,
    so this is intentionally reported as an attempt rather than a guaranteed
    connected call. It never claims that Gemini is on the call: this project
    has no audio bridge from Messenger into the Gemini Live session.
    """
    _require_pyautogui()
    if not _open_browser_url(_messenger_escalation_url(conversation_url)):
        return "Could not open Messenger for the call attempt."

    time.sleep(1.5)

    try:
        from actions.computer_control import _click, _screen_find

        coords = _screen_find("the audio or voice call button for this Messenger conversation")
        if not coords:
            return (
                f"Messenger opened for {receiver}, but the audio-call button "
                "was not found. No call was started."
            )
        _click(x=coords[0], y=coords[1])
        return (
            f"Messenger call attempt started for {receiver}. "
            f"Maximum intended duration: {max(1, int(max_duration_minutes))} minutes. "
            "Gemini is not connected to the call audio."
        )
    except Exception as e:
        return f"Messenger opened for {receiver}, but the call attempt failed: {e}"


def start_messenger_call_verified(
    receiver: str,
    max_duration_minutes: int = 5,
    conversation_url: str | None = None,
    diagnostic: bool = False,
) -> dict:
    """Start a Messenger call only after verifying contact and connection."""
    _require_pyautogui()
    try:
        direct_url = _messenger_escalation_url(conversation_url)
        if not _open_browser_url(direct_url):
            return {"connected": False, "detail": "Could not open Messenger Web."}
        print(f"MESSENGER_OPENING direct_url={direct_url}")
        time.sleep(1.5)
        contact_verdict = _screen_verdict(
            f"Is Messenger Web showing the expected conversation with '{receiver}' "
            "loaded from its direct conversation URL? Do not search for a contact "
            "or infer the conversation. Use UNKNOWN unless clearly visible.",
            ("YES", "NO", "UNKNOWN"),
        )
        if contact_verdict != "YES":
            return {
                "connected": False,
                "detail": f"Exact Messenger contact was not verified ({contact_verdict or 'UNKNOWN'}).",
            }

        fullscreen_ok, fullscreen_diagnostics = _enter_messenger_fullscreen()
        if not fullscreen_ok:
            return {
                "connected": False,
                "state": "MESSENGER_FULLSCREEN_FAILED",
                "detail": (
                    "Messenger fullscreen could not be verified; the call was "
                    "not started. "
                    f"{fullscreen_diagnostics.get('reason', '')}"
                ).strip(),
            }
        print("MESSENGER_SCREEN_CAPTURED coordinate_reference=fullscreen")
        from actions.computer_control import _click

        coords, _mapping = _find_messenger_call_button(diagnostic=diagnostic)
        if not coords:
            return {
                "connected": False,
                "state": "CALL_BUTTON_NOT_FOUND",
                "detail": "Messenger voice-call button could not be visually identified.",
            }
        print(f"MESSENGER_CALL_BUTTON_FOUND coordinates={coords}")
        cursor_before = pyautogui.position()
        if diagnostic:
            print(f"Current cursor X/Y: ({cursor_before[0]}, {cursor_before[1]})")
            pyautogui.moveTo(coords[0], coords[1], duration=0.3)
            time.sleep(0.7)
            cursor_moved = pyautogui.position()
            print(f"Cursor moved to: ({cursor_moved[0]}, {cursor_moved[1]})")
            print("CALL_BUTTON_CURSOR_VERIFIED diagnostic_only=true")
            return {
                "connected": None,
                "state": "CALL_BUTTON_HOVERED",
                "detail": (
                    "Messenger phone button located and the cursor was moved "
                    "to its calculated final coordinate. No click was attempted."
                ),
            }
        click_attempted = False
        try:
            print("CALL_BUTTON_CURSOR_VERIFIED diagnostic_only=false")
            _click(x=coords[0], y=coords[1])
            click_attempted = True
            print("CALL_INITIATED")
        finally:
            if diagnostic:
                print(f"Click attempted: {'yes' if click_attempted else 'no'}")

        # Messenger can spend several seconds transitioning from the header
        # button to its outgoing-call UI. Do not turn that normal transition
        # into a false failure.
        deadline = time.monotonic() + 12.0
        last_verdict = None
        saw_connecting = False
        while time.monotonic() < deadline:
            time.sleep(1.5)
            last_verdict = _screen_verdict(
                f"Inspect the visible Messenger Web UI for the call with '{receiver}'. "
                "Reply CONNECTED only when an active connected call is clearly "
                "shown. Reply CONNECTING when the outgoing-call UI is visible "
                "but it is still ringing, dialing, or connecting. Reply "
                "NOT_CONNECTED only when the call clearly failed, was declined, "
                "or ended. Reply UNKNOWN if the state cannot be determined.",
                ("CONNECTED", "CONNECTING", "NOT_CONNECTED", "UNKNOWN"),
            )
            if diagnostic:
                print(f"Visual/state change detected afterward: {last_verdict or 'UNKNOWN'}")
            if last_verdict == "CONNECTING":
                saw_connecting = True
                print("CALL_CONNECTING")
            if last_verdict == "CONNECTED":
                print("CALL_CONNECTED")
                return {
                    "connected": True,
                    "state": "CALL_CONNECTED",
                    "detail": (
                        f"Messenger confirmed connected to {receiver}; intended "
                        f"maximum duration is {max(1, int(max_duration_minutes))} minutes."
                    ),
                }
            if last_verdict == "NOT_CONNECTED":
                return {
                    "connected": False,
                    "state": "CALL_NOT_CONNECTED",
                    "detail": "Messenger call did not reach a connected state.",
                }
        final_state = (
            "CALL_CONNECTING"
            if saw_connecting
            else "CALL_UNKNOWN"
        )
        return {
            "connected": None,
            "state": final_state,
            "detail": (
                f"{final_state}: Messenger call connection could not be verified "
                f"(verdict: {last_verdict or 'UNKNOWN'})."
            ),
        }
    except Exception as e:
        return {"connected": False, "detail": f"Messenger call failed safely: {e}"}


def verify_messenger_call_connection(
    receiver: str, conversation_url: str | None = None
) -> dict:
    """Inspect Messenger for an existing call without starting another one."""
    _require_pyautogui()
    try:
        if not _open_browser_url(_messenger_escalation_url(conversation_url)):
            return {"connected": None, "detail": "Could not open Messenger for recovery verification."}
        time.sleep(1.0)
        verdict = _screen_verdict(
            f"Is the existing Messenger Web call in the expected conversation with "
            f"'{receiver}' clearly connected and in progress? Do not search or click "
            "anything. Reply "
            "CONNECTED, NOT_CONNECTED, or UNKNOWN.",
            ("CONNECTED", "NOT_CONNECTED", "UNKNOWN"),
        )
        return {
            "connected": (
                True if verdict == "CONNECTED"
                else False if verdict == "NOT_CONNECTED"
                else None
            ),
            "detail": f"Messenger recovery call verdict: {verdict or 'UNKNOWN'}.",
        }
    except Exception as e:
        return {"connected": None, "detail": f"Messenger recovery failed safely: {e}"}


def check_whatsapp_response(
    receiver: str, outgoing_message: str = ""
) -> bool | None:
    """Best-effort visual check for an incoming WhatsApp response.

    Returns True only when Gemini can see an incoming reply in the currently
    visible conversation. False means it could inspect the screen and did not
    see one; None means the check was unavailable or uncertain.
    """
    _require_pyautogui()
    try:
        from google.genai import types as gtypes
        from core import gemini

        image = pyautogui.screenshot()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        prompt = (
            "Inspect this screenshot of a desktop. Determine whether the currently "
            f"visible WhatsApp conversation with the exact contact named '{receiver}' "
            "clearly contains a NEW incoming reply from that contact after the "
            "current escalation's outgoing message. Do not count older replies "
            "above the outgoing message. "
            + (
                f"The current outgoing message text is {outgoing_message!r}. "
                if outgoing_message else ""
            )
            + "Reply with exactly YES or NO. Reply NO if the conversation is not "
            "visible or the result is uncertain."
        )
        response = gemini.call(
            [
                gtypes.Part.from_bytes(
                    data=buf.getvalue(), mime_type="image/png"
                ),
                prompt,
            ],
            tier=gemini.FAST,
            timeout_ms=20_000,
        )
        if response is None:
            return None
        answer = (response.text or "").strip().upper()
        if answer.startswith("YES"):
            return True
        if answer.startswith("NO"):
            return False
    except Exception as e:
        print(f"[SendMessage] ⚠️ WhatsApp response check unavailable: {e}")
    return None


_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                               _send_signal),
    ({"discord"},                              _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if any(k in key for k in keywords):
            return handler
    return lambda r, m: _desktop_send(platform_str.strip().title(), r, m)


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()

    if not receiver:
        return "Please specify a recipient."
    if not message_text:
        return "Please specify the message content."
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed — cannot control the desktop."

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")

    try:
        handler = _resolve_platform(platform)
        result  = handler(receiver, message_text)
    except Exception as e:
        result = f"Could not send message: {e}"

    print(f"[SendMessage] {'✅' if 'sent' in result.lower() else '❌'} {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "send_message",
    "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {
                "type": "STRING",
                "description": "Recipient contact name"
            },
            "message_text": {
                "type": "STRING",
                "description": "The message to send"
            },
            "platform": {
                "type": "STRING",
                "description": "Platform: WhatsApp, Telegram, etc."
            }
        },
        "required": [
            "receiver",
            "message_text",
            "platform"
        ]
    },
    "handler": send_message,
}
