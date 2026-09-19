"""
Telegram Connect for JARVIS.

This is deliberately a small, dependency-light adapter around the official
Telegram Bot API.  It uses long polling so the user's PC only makes outbound
connections; no public webhook or forwarded port is needed.

The plugin is discovered like every other file in plugins/.  Its optional
start()/stop() lifecycle is called by the plugin registry, while run() keeps
the normal plugin/tool contract intact for the assistant.
"""
from __future__ import annotations

import asyncio
import inspect
import mimetypes
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests


PLUGIN = {
    "name": "telegram_connect",
    "description": (
        "Reports the status of the Telegram remote-control connection. "
        "Telegram messages are already routed through the normal assistant "
        "session; do not use this tool to implement duplicate actions."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
        "required": [],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "telegram",
    "title": "Telegram Connect",
    "fields": [],
}

_API_ROOT = "https://api.telegram.org/bot{}"
_FILE_ROOT = "https://api.telegram.org/file/bot{}"
_POLL_TIMEOUT = 25
_MAX_MESSAGE = 4096
_MAX_DOWNLOAD = 50 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _log(logger: Callable[[str], None], message: str) -> None:
    try:
        logger(f"[Telegram] {message}")
    except Exception:
        pass


def _allowed_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    result: set[int] = set()
    for value in raw.split(","):
        try:
            result.add(int(value.strip()))
        except (TypeError, ValueError):
            if value.strip():
                continue
    return result


def _split_message(text: str, limit: int = _MAX_MESSAGE) -> list[str]:
    """Split without truncating, preferring paragraph and line boundaries."""
    text = str(text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = max(
            text.rfind("\n\n", 0, limit + 1),
            text.rfind("\n", 0, limit + 1),
            text.rfind(" ", 0, limit + 1),
        )
        if cut < max(1, limit // 2):
            cut = limit
        chunk = text[:cut].rstrip()
        if chunk:
            chunks.append(chunk)
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def _safe_filename(name: str, fallback: str = "telegram_file") -> str:
    name = _SAFE_NAME.sub("_", Path(name or "").name).strip("._")
    return name[:160] or fallback


class _TelegramClient:
    def __init__(
        self,
        token: str,
        allowed_ids: set[int],
        message_handler: Callable,
        status_provider: Callable[[], str],
        clear_handler: Callable[[int], str],
        logger: Callable[[str], None],
    ):
        self.token = token
        self.allowed_ids = allowed_ids
        self.message_handler = message_handler
        self.status_provider = status_provider
        self.clear_handler = clear_handler
        self.logger = logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._session = requests.Session()
        self._offset = 0
        self._username = ""
        self._started_at = time.monotonic()
        self._logged_unauthorized: set[int] = set()

    @property
    def username(self) -> str:
        return self._username

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="telegram-connect",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._session.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    def _request(self, method: str, **payload) -> dict:
        response = self._session.post(
            _API_ROOT.format(self.token) + "/" + method,
            json=payload,
            timeout=(_POLL_TIMEOUT + 10 if method == "getUpdates" else 20),
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(str(body.get("description") or "Telegram API error"))
        return body.get("result")

    def _send_text(self, chat_id: int, text: str) -> None:
        for chunk in _split_message(text):
            self._request("sendMessage", chat_id=chat_id, text=chunk)

    def _send_photo(self, chat_id: int, data: bytes, mime_type: str) -> None:
        response = self._session.post(
            _API_ROOT.format(self.token) + "/sendPhoto",
            data={"chat_id": str(chat_id)},
            files={"photo": ("jarvis-capture.jpg", data, mime_type or "image/jpeg")},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(str(body.get("description") or "Telegram API error"))

    def _send_typing(self, chat_id: int) -> None:
        try:
            self._request("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:
            pass

    def _download_attachment(self, message: dict, user_id: int) -> Optional[str]:
        document = message.get("document")
        photo = message.get("photo")
        if document:
            file_id = document.get("file_id")
            original_name = document.get("file_name") or "telegram_document"
        elif photo:
            largest = photo[-1]
            file_id = largest.get("file_id")
            original_name = "telegram_photo.jpg"
        else:
            return None
        if not file_id:
            return None

        file_info = self._request("getFile", file_id=file_id)
        file_path = file_info.get("file_path") if isinstance(file_info, dict) else None
        if not file_path:
            return None
        if int(file_info.get("file_size") or 0) > _MAX_DOWNLOAD:
            raise RuntimeError("the Telegram attachment is larger than 50 MB")

        base = Path(__file__).resolve().parent.parent / "downloads" / "telegram" / str(user_id)
        base.mkdir(parents=True, exist_ok=True)
        suffix = Path(file_path).suffix or mimetypes.guess_extension(
            message.get("document", {}).get("mime_type", "") if document else "image/jpeg"
        ) or ""
        destination = base / _safe_filename(original_name + (suffix if "." not in original_name else ""))
        with self._session.get(
            _FILE_ROOT.format(self.token) + "/" + file_path,
            stream=True,
            timeout=30,
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                size = 0
                for block in response.iter_content(64 * 1024):
                    size += len(block)
                    if size > _MAX_DOWNLOAD:
                        raise RuntimeError("the Telegram attachment is larger than 50 MB")
                    output.write(block)
        return str(destination)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message") or {}
        sender = message.get("from") or {}
        user_id = sender.get("id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(user_id, int) or chat_id is None:
            return

        if user_id not in self.allowed_ids:
            if user_id not in self._logged_unauthorized:
                self._logged_unauthorized.add(user_id)
                _log(self.logger, f"Unauthorized user attempted access: {user_id}")
            return

        text = str(message.get("text") or message.get("caption") or "").strip()
        if text.startswith("/"):
            command = text.split()[0].split("@", 1)[0].lower()
            if command == "/start":
                self._send_text(
                    chat_id,
                    "JARVIS Remote Control\n\nConnected successfully.\n\n"
                    "Send me a message and I’ll process it through your AI Assistant.",
                )
                return
            if command == "/help":
                self._send_text(
                    chat_id,
                    "Available commands:\n"
                    "/status — show assistant status\n"
                    "/ping — test the connection\n"
                    "/clear — reset the shared assistant conversation\n"
                    "/help — show this help\n\n"
                    "You can also send a normal message or an attachment.",
                )
                return
            if command == "/status":
                self._send_text(chat_id, self.status_provider())
                return
            if command == "/ping":
                self._send_text(chat_id, "JARVIS is online.")
                return
            if command == "/clear":
                self._send_text(chat_id, self.clear_handler(user_id))
                return

        if not text and not (message.get("document") or message.get("photo")):
            return

        self._send_typing(chat_id)
        _log(self.logger, "Message received.")
        try:
            attachment = self._download_attachment(message, user_id)
            if attachment:
                text = (
                    f"{text}\n\n"
                    f"[Telegram attachment downloaded to: {attachment}]\n"
                    "Use the existing file processor if this request refers to the attachment."
                ).strip()
            _log(self.logger, "Processing request.")
            result = self.message_handler(text, user_id)
            if hasattr(result, "result") and callable(result.result):
                # The lifecycle runs in a worker thread; the main module supplies
                # a thread-safe bridge that returns a concurrent Future.
                result = result.result(timeout=300)
            elif inspect.isawaitable(result):
                result = asyncio.run(result)
            if isinstance(result, dict):
                self._send_text(chat_id, str(result.get("text") or "Done."))
                for media in result.get("media") or []:
                    self._send_photo(chat_id, media[0], media[1])
            else:
                self._send_text(chat_id, str(result or "Done."))
            _log(self.logger, "Response sent.")
        except Exception as exc:
            _log(self.logger, f"Request failed: {type(exc).__name__}")
            self._send_text(
                chat_id,
                "I couldn't complete that request. The assistant is still running; "
                "please try again.",
            )

    def _poll_loop(self) -> None:
        backoff = 3
        first_connection = True
        while not self._stop.is_set():
            try:
                if first_connection:
                    _log(self.logger, "Connecting...")
                    info = self._request("getMe")
                    self._username = str((info or {}).get("username") or "")
                    _log(self.logger, f"Connected as @{self._username or 'unknown'}")
                    _log(self.logger, "Listening for messages.")
                    first_connection = False
                updates = self._request(
                    "getUpdates", offset=self._offset, timeout=_POLL_TIMEOUT,
                    allowed_updates=["message"],
                )
                backoff = 3
                for update in updates or []:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    self._handle_update(update)
            except Exception as exc:
                if self._stop.is_set():
                    break
                _log(self.logger, "Connection lost.")
                _log(self.logger, "Reconnecting...")
                _log(self.logger, f"API error: {type(exc).__name__}")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
                first_connection = True


_client: Optional[_TelegramClient] = None
_client_lock = threading.Lock()
_runtime_status: Callable[[], str] = lambda: "JARVIS\n● Online\nTelegram: Connected"
_runtime_clear: Callable[[int], str] = lambda _user_id: "Telegram context reset."
_runtime_handler: Optional[Callable] = None
_runtime_logger: Callable[[str], None] = print


def configure(
    message_handler: Callable,
    status_provider: Callable[[], str],
    clear_handler: Callable[[int], str],
    logger: Callable[[str], None] = print,
) -> None:
    global _runtime_handler, _runtime_status, _runtime_clear, _runtime_logger
    _runtime_handler = message_handler
    _runtime_status = status_provider
    _runtime_clear = clear_handler
    _runtime_logger = logger


def start(
    message_handler: Optional[Callable] = None,
    status_provider: Optional[Callable[[], str]] = None,
    clear_handler: Optional[Callable[[int], str]] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> None:
    """Start polling, or report why the optional integration is disabled."""
    global _client
    if message_handler:
        configure(message_handler, status_provider or _runtime_status,
                  clear_handler or _runtime_clear, logger or _runtime_logger)
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    allowed = _allowed_ids()
    if not token:
        _log(_runtime_logger, "Disabled: TELEGRAM_BOT_TOKEN is not configured.")
        return
    if not allowed:
        _log(_runtime_logger, "Disabled: TELEGRAM_ALLOWED_USER_IDS is not configured.")
        return
    if _runtime_handler is None:
        _log(_runtime_logger, "Disabled: no assistant message handler is available.")
        return
    with _client_lock:
        if _client is None:
            _client = _TelegramClient(
                token, allowed, _runtime_handler, _runtime_status,
                _runtime_clear, _runtime_logger,
            )
            _client.start()
    _log(_runtime_logger, "Plugin loaded.")


def stop() -> None:
    global _client
    with _client_lock:
        client, _client = _client, None
    if client:
        client.stop()


def run(parameters: dict, player=None, session_memory=None) -> str:
    if _client and _client.username:
        return f"Telegram is connected as @{_client.username}."
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        return "Telegram is configured and connecting."
    return "Telegram is disabled because TELEGRAM_BOT_TOKEN is not configured."