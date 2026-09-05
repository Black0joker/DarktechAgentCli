
import json
import os

import requests

from app.agent.configData import (
    get_active_token,
    get_proxy_url,
)
from app.agent.logger import Logger


class ServiceUnavailableError(Exception):
    """Raised when the proxy returns HTTP 503 (maintenance / temporarily unavailable)."""

    def __init__(self, detail: str = "The service is temporarily unavailable for maintenance. Please try again later."):
        self.detail = detail
        super().__init__(detail)


# MIME types for image uploads (same mapping as DarktechClient.upload_image)
_IMAGE_CONTENT_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.bmp': 'image/bmp',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.tiff': 'image/tiff',
    '.tif': 'image/tiff',
}


class ProxyClient:
    """Client for the Darktech Chat service via the reverse-proxy REST API."""
    
    def __init__(self, model="darktech_v3", mode="normal", base_url=None):
        self.base_url = (base_url or get_proxy_url()).rstrip('/')
        self.session = requests.Session()
        # Kept for compatibility with the tokenless-startup guard used by the
        # UI layer (no token configured).
        self.Mode = mode   # "normal" or "thinking"
        self.model = model

    # ------------------------------------------------------------------
    # Request plumbing
    # ------------------------------------------------------------------

    def _headers(self, extra=None) -> dict:
        """Build per-request headers with a fresh Bearer token.

        The token is read from config on every request (never cached) so that
        runtime token rotation (rate-limit failover, /change_token,
        /add_token) is reflected without recreating the client.
        """
        headers = {'Accept': 'application/json'}
        token = get_active_token()
        if token:
            headers['Authorization'] = f'Bearer {token}'
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method, path, timeout=60, **kwargs):
        """Centralized request helper with Bearer auth and error logging."""
        url = f"{self.base_url}{path}"
        extra_headers = kwargs.pop('headers', None)
        kwargs['headers'] = self._headers(extra_headers)
        kwargs.setdefault('timeout', timeout)
        try:
            response = self.session.request(method, url, **kwargs)
            # Detect 503 Service Unavailable (maintenance) from any endpoint.
            # The proxy returns: {"detail": "The service is temporarily unavailable..."}
            if response.status_code == 503:
                try:
                    body = response.json()
                    detail = body.get('detail', response.text) if isinstance(body, dict) else response.text
                except Exception:
                    detail = response.text or "The service is temporarily unavailable for maintenance. Please try again later."
                Logger.error(f"Proxy API 503 Service Unavailable: {method} {path}: {detail}")
                raise ServiceUnavailableError(detail)
            response.raise_for_status()
            return response
        except ServiceUnavailableError:
            raise
        except requests.exceptions.RequestException as e:
            body = ''
            err_response = getattr(e, 'response', None)
            if err_response is not None:
                try:
                    body = f" | body: {err_response.text[:300]}"
                except Exception:
                    body = ''
            Logger.error(f"Proxy API request failed: {method} {path}: {e}{body}")
            raise

    # ------------------------------------------------------------------
    # Chats
    # ------------------------------------------------------------------

    def create_chat(self):
        """Create a new chat session. Returns the chat id."""
        json_data = {
            'model': self.model,
            'mode': self.Mode if self.Mode in ('normal', 'thinking') else 'normal',
        }
        response = self._request("POST", "/api/v1/chats/new", json=json_data)
        chat_id = response.json()['chat_id']
        Logger.success(f"Chat created via proxy: {chat_id}")
        return chat_id

    def check_chat_exists(self, chat_id: str) -> bool:
        """Check if a chat exists on the server.

        Endpoint: GET /api/v1/chats/{chat_id}/exists
        Returns True if the chat exists, False otherwise.
        """
        Logger.info(f"Checking if chat exists: {chat_id}")
        try:
            response = self._request("GET", f"/api/v1/chats/{chat_id}/exists")
            exists = response.json().get('exists', False)
            Logger.info(f"Chat {chat_id} exists: {exists}")
            return exists
        except requests.exceptions.RequestException as e:
            Logger.warn(f"Failed to check chat existence for {chat_id}: {e}")
            return False

    def get_chat_title(self, chat_session_id):
        """Get the auto-generated title for a chat session.

        Endpoint: GET /api/v1/chats/{chat_id}/chatTitle
        Returns the title string, or None on failure.
        """
        Logger.info(f"Fetching chat title for session: {chat_session_id}")
        try:
            response = self._request("GET", f"/api/v1/chats/{chat_session_id}/chatTitle")
            title = response.json().get('title')
            Logger.success(f"Fetched chat title for session {chat_session_id}: {title}")
            return title
        except requests.exceptions.RequestException as e:
            Logger.warn(f"Failed to fetch chat title for session {chat_session_id}: {e}")
            return None

    def get_chat_parent_Id(self, chat_session_id):
        """Get the current (parent) message id for a chat session."""
        Logger.info(f"Fetching parent message id for session: {chat_session_id}")
        response = self._request("GET", f"/api/v1/chats/{chat_session_id}/parent")
        parent_id = response.json().get('parent_id')
        Logger.success(f"Fetched parent id for session: {chat_session_id}")
        return parent_id


    def delete_chat(self, chat_session_id):
        """Delete a chat session."""
        Logger.info(f"Deleting chat session: {chat_session_id}")
        response = self._request("DELETE", f"/api/v1/chats/{chat_session_id}")
        data = response.json()
        Logger.success(f"Deleted chat session: {chat_session_id}")
        return data

    def upload_chat_cache(self, chat_cache: dict) -> dict:
        """Upload a single chat from the internal cache format via POST /api/v1/chats/upload-cache.

        The proxy maps the cache payload ({chat_id, title, messages[]}) to the
        messages/import format and uploads it. See docs/upload-chat-api.md.

        Args:
            chat_cache: A dict shaped exactly like the on-disk cache file:
                        {"chat_id": "...", "title": "...", "messages": [{...}, ...]}.

        Returns the upstream import result dict on success.
        Raises on request or HTTP failure.
        """
        Logger.info(f"Uploading chat cache via proxy: chat_id={chat_cache.get('chat_id')}, "
                    f"title={chat_cache.get('title')}, "
                    f"messages={len(chat_cache.get('messages', []))}")
        try:
            response = self._request(
                "POST", "/api/v1/chats/upload-cache", json=chat_cache, timeout=120
            )
        except requests.exceptions.HTTPError as e:
            # Include the response body in the error so callers/UI can display
            # the server's detailed error message (e.g. validation failures).
            err_response = getattr(e, 'response', None)
            body_text = ''
            if err_response is not None:
                try:
                    body_text = err_response.text[:500]
                except Exception:
                    body_text = ''

            # Handle "chat already exists" gracefully: the chat is already on
            # the server, so treat it as a successful no-op upload.
            if err_response is not None and err_response.status_code == 400 and 'already exists' in body_text.lower():
                chat_id = chat_cache.get('chat_id', '')
                Logger.info(f"Chat {chat_id} already exists on server, skipping upload.")
                # Fetch the parent_message_id from the existing chat
                parent_id = None
                try:
                    parent_id = self.get_chat_parent_Id(chat_id)
                except Exception:
                    pass
                return {'chat_id': chat_id, 'parent_message_id': parent_id, 'already_exists': True}

            error_msg = f"{e}"
            if body_text:
                error_msg += f" | body: {body_text}"
            Logger.error(f"upload_chat_cache failed: {error_msg}")
            raise requests.exceptions.HTTPError(error_msg, response=err_response) from e
        data = response.json()
        Logger.success(f"Chat cache uploaded via proxy: {str(data)[:200]}")
        return data

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def upload_image(self, file_path: str) -> dict:
        """Upload an image via POST /api/v1/files/upload-image (multipart).

        Returns the upload response dict containing ``fileId`` (a list of
        uploaded file IDs). Extract the ``fileId`` values and pass them as
        the ``file_ids`` array in send_prompt.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Image file not found: {file_path}")

        filename = os.path.basename(file_path)
        filesize = os.path.getsize(file_path)
        ext = os.path.splitext(filename)[1].lower()
        content_type = _IMAGE_CONTENT_TYPES.get(ext, 'image/jpeg')

        Logger.info(f"Uploading image via proxy: {file_path} ({filesize} bytes, {content_type})")
        with open(file_path, 'rb') as f:
            response = self._request(
                "POST",
                "/api/v1/files/upload-image",
                files={'file': (filename, f, content_type)},
                timeout=300,
            )
        file_obj = response.json()
        if not isinstance(file_obj, dict):
            file_obj = {'type': 'image', 'url': str(file_obj)}
        # Ensure the keys consumed by the UI are present
        file_obj.setdefault('type', 'image')
        file_obj.setdefault('name', filename)
        file_obj.setdefault('size', filesize)
        Logger.success(f"Image uploaded via proxy: {filename} -> {file_obj.get('url', '')}")
        return file_obj

    def upload_file_content(self, content: str, filename: str = 'tool_output.txt') -> dict:
        """Upload text content as a file via POST /api/v1/files/upload.

        Returns the uploaded file object.
        """
        Logger.info(f"Uploading file content via proxy: {filename} ({len(content)} chars)")
        response = self._request(
            "POST",
            "/api/v1/files/upload",
            json={'content': content, 'filename': filename},
            timeout=120,
        )
        data = response.json()
        file_obj = data.get('file', data) if isinstance(data, dict) else data
        Logger.success(f"File content uploaded via proxy: {filename}")
        return file_obj

    # ------------------------------------------------------------------
    # Completions
    # ------------------------------------------------------------------

    def send_prompt(self, prompt, chat_id, parent_message_id=None, file_ids=None):
        """Send a prompt and return the SSE stream response.

        On upstream failures (auth errors, captcha, rate limits, 5xx) the
        proxy responds with a non-SSE JSON error envelope; in that case a
        dict ``{"error": "..."}`` is returned instead of a stream, mirroring
        DarktechClient.send_prompt's error contract.

        ``file_ids`` is an optional list of uploaded file IDs to attach to
        the message payload.
        """
        if file_ids:
            Logger.info(f"send_prompt(proxy): attaching {len(file_ids)} file id(s) to message payload")

        json_data = {
            'prompt': prompt,
            'chat_id': chat_id,
            'parent_message_id': parent_message_id,
            'file_ids': file_ids if file_ids else [],
            'model': self.model,
            'mode': self.Mode if self.Mode in ('normal', 'thinking') else 'normal',
        }

        # Connect timeout only: the SSE stream stays open for the whole generation.
        try:
            stream = self.session.post(
                f"{self.base_url}/api/v1/chat/completions",
                json=json_data,
                headers=self._headers(),
                stream=True,
                timeout=(15, None),
            )
        except requests.exceptions.RequestException as conn_err:
            Logger.error(f"send_prompt(proxy) connection error: {conn_err}")
            raise

        # Detect 503 Service Unavailable (maintenance) before inspecting Content-Type.
        if stream.status_code == 503:
            try:
                body = stream.json()
                detail = body.get('detail', stream.text) if isinstance(body, dict) else stream.text
            except Exception:
                detail = stream.text or "The service is temporarily unavailable for maintenance. Please try again later."
            Logger.error(f"send_prompt(proxy) 503 Service Unavailable: {detail}")
            stream.close()
            raise ServiceUnavailableError(detail)

        content_type = stream.headers.get("Content-Type", "")
        if "text/event-stream" not in content_type:
            # Non-SSE response: JSON error envelope from the proxy/upstream.
            try:
                data = stream.json()
            except Exception:
                data = {'detail': stream.text[:500]}
            Logger.error(f"send_prompt(proxy) error HTTP {stream.status_code}: {json.dumps(data)[:500]}")
            envelope = data.get('detail', data) if isinstance(data, dict) else {'detail': str(data)}
            # Captcha challenge: the proxy wraps the upstream punish payload as
            # {"detail": {"error": "captcha", "url": "<punish url>"}} (HTTP 429).
            # Surface the same contract the wrapper expects so it can delegate
            # to app/agent/captcha_solver.py and retry.
            if isinstance(envelope, dict) and envelope.get('error') == 'captcha':
                Logger.warn("send_prompt(proxy): captcha challenge received, delegating to captcha solver")
                return {"error": "captcha", "url": envelope.get('url', '')}
            # Daily usage rate limit: {"success": false, "data": {"code": "RateLimited", "num": 6}}
            rate_data = data.get('data') if isinstance(data, dict) else None
            if isinstance(rate_data, dict) and rate_data.get('code') == 'RateLimited':
                Logger.warn("send_prompt(proxy): RateLimited - daily usage limit reached")
                return {
                    "error": "RateLimited",
                    "details": rate_data.get('details', ''),
                    "num": rate_data.get('num'),
                }
            if isinstance(envelope, dict):
                detail = envelope.get('detail', json.dumps(envelope))
            else:
                detail = str(envelope)
            return {"error": f"Proxy error (HTTP {stream.status_code}): {detail}"}
        return stream

    def StopSendingPrompt(self, chat_id, parent_message_id):
        """Abort a running generation via POST /api/v1/chat/completions/stop.

        The proxy requires both fields, so the call is skipped when either id
        is unavailable (nothing to stop yet).
        """
        if not chat_id or not parent_message_id:
            return
        json_data = {
            'chat_id': chat_id,
            'parent_message_id': parent_message_id,
        }
        try:
            self._request("POST", "/api/v1/chat/completions/stop", json=json_data, timeout=30)
        except requests.exceptions.RequestException as e:
            Logger.warn(f"StopSendingPrompt via proxy failed (ignored): {e}")

    def close(self):
        """Close the underlying HTTP session to release resources."""
        if self.session:
            self.session.close()
