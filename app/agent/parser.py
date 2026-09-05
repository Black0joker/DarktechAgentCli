import json
from pathlib import Path
import sys
from typing import Generator

from app.agent.logger import Logger

def parse_proxy_stream(response) -> Generator[dict, None, None]:
    """Parse the high-level SSE events emitted by the Darktech proxy API (docs/API.md).

    This generator simply unwraps those lines so the AgentWrapper can consume
    proxy streams and direct streams identically. A trailing ``closed`` event
    is guaranteed even if the connection drops early.
    """
    closed = False
    try:
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                Logger.warn(f"[proxy] Skipping malformed SSE payload: {payload[:200]}")
                continue
            if not isinstance(event, dict) or 'type' not in event:
                continue
            if event.get('type') == 'action':
                # Normalize the legacy wire name documented in docs/API.md
                # to the 'tool' event type consumed by AgentWrapper.
                event = dict(event)
                event['type'] = 'tool'
            yield event
            if event.get('type') == 'closed':
                closed = True
                break
    finally:
        try:
            response.close()
        except Exception:
            pass
    if not closed:
        yield {"type": "closed"}