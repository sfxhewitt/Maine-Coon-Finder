"""Telegram Bot API, called directly over HTTPS (no extra library)."""

from __future__ import annotations

import html
import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


class Telegram:
    def __init__(self, token: str, chat_id: str = "", timeout: float = 60.0):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self._client = httpx.Client(timeout=timeout)

    def _call(self, method: str, files: dict | None = None, **params: Any) -> Any:
        data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
        r = self._client.post(f"{self.base}/{method}", data=data, files=files)
        try:
            body = r.json()
        except ValueError as e:
            raise TelegramError(f"{method}: HTTP {r.status_code}") from e
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description')}")
        return body["result"]

    # --- sending ---
    def send_text(self, text: str, reply_to: int | None = None, keyboard: list | None = None, chat_id: str | None = None) -> dict:
        return self._call(
            "sendMessage",
            chat_id=chat_id or self.chat_id,
            text=text[:4096],
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_to_message_id=reply_to,
            reply_markup={"inline_keyboard": keyboard} if keyboard else None,
        )

    def send_photo(self, photo: bytes | None, caption: str, keyboard: list | None = None, photo_url: str | None = None) -> dict:
        markup = {"inline_keyboard": keyboard} if keyboard else None
        if photo:
            return self._call(
                "sendPhoto",
                files={"photo": ("kitten.jpg", photo, "image/jpeg")},
                chat_id=self.chat_id,
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=markup,
            )
        if photo_url:
            return self._call(
                "sendPhoto", chat_id=self.chat_id, photo=photo_url, caption=caption[:1024],
                parse_mode="HTML", reply_markup=markup,
            )
        return self.send_text(caption, keyboard=keyboard)

    def edit_caption(self, message_id: int, caption: str, keyboard: list | None = None) -> None:
        try:
            self._call(
                "editMessageCaption",
                chat_id=self.chat_id,
                message_id=message_id,
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup={"inline_keyboard": keyboard or []},
            )
        except TelegramError:
            # Text-only alerts have no caption; edit the text instead.
            try:
                self._call(
                    "editMessageText",
                    chat_id=self.chat_id,
                    message_id=message_id,
                    text=caption[:4096],
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup={"inline_keyboard": keyboard or []},
                )
            except TelegramError as e:
                log.warning("couldn't edit alert %s: %s", message_id, e)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])
        except TelegramError as e:
            log.debug("answerCallbackQuery failed: %s", e)

    # --- receiving ---
    def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return self._call(
            "getUpdates",
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "callback_query"],
        )

    def set_commands(self) -> None:
        cmds = [
            {"command": "status", "description": "What the finder has seen so far"},
            {"command": "pause", "description": "Stop sending alerts"},
            {"command": "resume", "description": "Start sending alerts again"},
            {"command": "test", "description": "Send a test alert (no email is sent)"},
        ]
        try:
            self._call("setMyCommands", commands=cmds)
        except TelegramError as e:
            log.debug("setMyCommands failed: %s", e)
