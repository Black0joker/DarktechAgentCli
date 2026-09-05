import json
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

from app.agent.logger import Logger

# Cache directory: relative to the exe (PyInstaller bundle) or the project root (dev).
# Mirrors the _BASE_DIR logic in configData.py so the cache lives next to config/.
if getattr(sys, 'frozen', False):
    _BASE_DIR = Path(sys.executable).parent
else:
    _BASE_DIR = Path(__file__).parent.parent.parent

CACHE_DIR = _BASE_DIR / "chat_cache"


class ChatCache:
    """Manages per-chat message caching to disk.

    Each chat session gets its own JSON file named `{chat_id}.json` inside
    the `chat_cache/` directory.  Messages are appended incrementally so
    the cache stays in sync with the live conversation.
    """

    def __init__(self, chat_id: str, model_name: str, thinking_mode: bool):
        self.chat_id = chat_id
        self.model_name = model_name
        self.thinking_mode = thinking_mode
        self.title: Optional[str] = None
        self._messages: List[Dict[str, Any]] = []
        self._file_path = CACHE_DIR / f"{chat_id}.json"

        # Accumulators for the current assistant turn
        self._summary_titles: List[str] = []
        self._summary_thoughts: List[str] = []
        self._stream_text_parts: List[str] = []
        self._current_message_id: Optional[str] = None
        self._current_parent_message_id: Optional[str] = None
        self._last_usage: Optional[Dict[str, Any]] = None

        # Load existing cache if present
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self):
        """Load existing cache from disk if available."""
        if self._file_path.exists():
            try:
                with open(self._file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._messages = data.get("messages", [])
                self.title = data.get("title")
                Logger.info(f"Chat cache loaded: {self._file_path} ({len(self._messages)} messages)")
            except (json.JSONDecodeError, IOError) as e:
                Logger.warn(f"Failed to load chat cache {self._file_path}: {e}")
                self._messages = []

    def _save(self):
        """Persist current messages to disk."""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            payload: Dict[str, Any] = {"chat_id": self.chat_id, "title": self.title, "messages": self._messages}
            with open(self._file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except IOError as e:
            Logger.error(f"Failed to save chat cache {self._file_path}: {e}")

    # ------------------------------------------------------------------
    # Accumulation helpers (called during streaming)
    # ------------------------------------------------------------------

    def set_title(self, title: str):
        """Set the chat title and persist to disk."""
        self.title = title
        self._save()
        Logger.info(f"[ChatCache] Title updated: '{title}' ({self.chat_id})")

    def set_message_ids(self, message_id: str, parent_message_id: Optional[str]):
        """Track the current response/message IDs from the stream."""
        self._current_message_id = message_id
        self._current_parent_message_id = parent_message_id

    def set_response_id(self, response_id: str):
        """Set only the message_id (from response_id event)."""
        self._current_message_id = response_id

    def set_parent_message_id(self, parent_id: str):
        """Set only the parent_message_id (from parent_message_id event)."""
        self._current_parent_message_id = parent_id

    def add_summary_title(self, title: str):
        """Accumulate a summary_title event."""
        if title:
            self._summary_titles.append(title)

    def add_summary_thought(self, thought: str):
        """Accumulate a summary_thought event."""
        if thought:
            self._summary_thoughts.append(thought)

    def add_stream_text(self, text: str):
        """Accumulate a stream_text chunk."""
        if text:
            self._stream_text_parts.append(text)

    def set_usage(self, usage: Optional[Dict[str, Any]]):
        """Track the latest token-usage object emitted by the stream.

        The stream delivers a ``tokens`` event whose value is the usage
        object (e.g. {"total_tokens": ...}); the last one received for the
        turn is attached to the assistant message.
        """
        if usage:
            self._last_usage = usage

    # ------------------------------------------------------------------
    # Message caching
    # ------------------------------------------------------------------

    _UNSET = object()  # Sentinel to distinguish "not provided" from explicit None

    def backfill_user_message_id(self, message_id: Optional[str]):
        """Backfill the message_id of the most recent cached user message.

        The server-assigned id of a user message is unknown at send time; it
        is revealed by the assistant's ``parent_message_id`` stream event.
        Until then the cached user entry stores null. This method fills it in
        retroactively so the cache stays consistent.
        """
        if not message_id:
            return
        for entry in reversed(self._messages):
            if entry.get("role") == "user":
                if entry.get("message_id") is None:
                    entry["message_id"] = message_id
                    self._save()
                    Logger.info(f"[ChatCache] Backfilled user message_id={message_id} ({self.chat_id})")
                break

    def cache_user_message(self, content: str, message_id=_UNSET, parent_message_id=_UNSET):
        """Cache a user message.

        If message_id / parent_message_id are not provided, fall back to the
        currently tracked IDs.  Pass explicit None to store null.
        """
        resolved_message_id = self._current_message_id if message_id is self._UNSET else message_id
        resolved_parent_id = self._current_parent_message_id if parent_message_id is self._UNSET else parent_message_id
        entry = {
            "message_id": resolved_message_id,
            "parent_message_id": resolved_parent_id,
            "role": "user",
            "model_name": self.model_name,
            "thinking_mode": self.thinking_mode,
            "content": content,
        }
        self._messages.append(entry)
        self._save()
        Logger.info(f"[ChatCache] Cached user message ({self.chat_id})")

    def cache_assistant_message(self, tool_results: Optional[Any] = None, final_json: Optional[Dict] = None):
        """Cache an assistant message.

        Content resolution:
        - If thinking_mode is True, include summary_title / summary_thought.
        - If tool_results is provided, content = the tool results JSON string.
        - Otherwise, collect stream_text parts and store as the final JSON.
        """
        content: Any = None

        if tool_results is not None:
            # Assistant turn that produced tool calls; content is the results
            # prompt that will be sent back to the model.
            content = tool_results if isinstance(tool_results, str) else json.dumps(tool_results, ensure_ascii=False)
        elif final_json is not None:
            # Final structured JSON response (e.g. user_response / ask_user)
            content = final_json
        elif self._stream_text_parts:
            # Collect streamed text into a single string
            content = "".join(self._stream_text_parts)
        else:
            content = ""

        entry: Dict[str, Any] = {
            "message_id": self._current_message_id,
            "parent_message_id": self._current_parent_message_id,
            "role": "assistant",
            "model_name": self.model_name,
            "thinking_mode": self.thinking_mode,
        }

        # Attach thinking summaries when in thinking mode
        if self.thinking_mode:
            thinking_content: Dict[str, Any] = {}
            if self._summary_titles:
                thinking_content["summary_title"] = {"content": list(self._summary_titles)}
            if self._summary_thoughts:
                thinking_content["summary_thought"] = {"content": list(self._summary_thoughts)}
            if thinking_content:
                entry["thinking"] = thinking_content

        entry["content"] = content

        # Attach the last usage object (token counts) for this turn
        if self._last_usage:
            entry["usage"] = self._last_usage

        self._messages.append(entry)
        self._save()
        Logger.info(f"[ChatCache] Cached assistant message ({self.chat_id})")

    # ------------------------------------------------------------------
    # Reset per-turn accumulators
    # ------------------------------------------------------------------

    def reset_turn(self):
        """Reset accumulators for a new assistant turn."""
        self._summary_titles.clear()
        self._summary_thoughts.clear()
        self._stream_text_parts.clear()
        self._last_usage = None

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def messages(self) -> List[Dict[str, Any]]:
        return list(self._messages)

    @property
    def file_path(self) -> Path:
        return self._file_path
