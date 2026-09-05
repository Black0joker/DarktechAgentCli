import sys
import os
import json
import asyncio
import requests
import traceback
from pathlib import Path
from typing import Callable, Dict, Any, Optional

# Import agent modules
from app.agent.module import execute_tool, create_client, EXPECTED_TOOLS
from app.agent.logger import Logger
from app.agent.parser import parse_proxy_stream
from app.agent.chat_cache import ChatCache

class AgentWrapper:
    """
    Wraps the autonomous agent for integration with the Textual UI.
    
    The agent runs as an asyncio task and communicates with the UI via callbacks.
    Uses async/await for better concurrency control instead of threading.
    """
    
    def __init__(self, app, response_timeout=300):
        self.app = app
        self.client = None
        self.session_id = None
        self.parent_message_id = None
        self.previous_message_id=None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.system_prompt = None
        self.callbacks = {}
        self.pending_response = None
        self.response_event = asyncio.Event()
        self.response_timeout = response_timeout
        # Tool permission tracking
        # Pre-allowed tools that don't require permission (read-only operations)
        self.allowed_tools_session = {'current_path', 'list_directory', 'read_file', 'enter_plan_mode'}
        self.pending_tool_permission = None
        self.tool_permission_event = asyncio.Event()
        # Permission mode: "auto" (all tools allowed) or "read-only" (only read-only tools allowed, others ask permission)
        self.permission_mode = "auto"
        # Token tracking: accumulated total across all loop iterations
        self.total_tokens = 0
        # Mode tracking: "normal" or "thinking"
        self.mode = "thinking"
        self.model = "darktech_v3"
        # Pending file attachments (list of file.json-style dicts)
        self.pending_files = []
        # Pending file IDs extracted from upload responses (for send_prompt file_ids)
        self.pending_file_ids = []
        # Chat cache instance (created per session)
        self.chat_cache: Optional[ChatCache] = None
        # Track whether the chat title has been fetched for the current session
        self._title_fetched = False
    
    def _get_allowed_tools(self):
        """Getter for allowed_tools_session."""
        return set(self.allowed_tools_session)
    
    def _add_allowed_tool(self, tool: str):
        """Adder for allowed_tools_session."""
        self.allowed_tools_session.add(tool)
    
    @property
    def running(self):
        return self._running
    
    @running.setter
    def running(self, value):
        self._running = value
        
    def initialize(self):
        """Initialize the agent client (without creating a chat session)."""
        try:
            # Step 1: Create client
            Logger.info("Creating agent client...")
            self.client = create_client(mode=self.mode)
            Logger.success("Agent client initialized successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            Logger.error(f"Network error during initialization: {e}")
            return False
        except FileNotFoundError as e:
            Logger.error(f"File not found during initialization: {e}")
            return False
        except KeyError as e:
            Logger.error(f"Missing expected data in API response: {e}")
            return False
        except Exception as e:
            Logger.error(f"Unexpected error during initialization: {e}")
            import traceback
            Logger.error(traceback.format_exc())
            return False
    
    def _ensure_chat_session(self):
        """Create a new chat session if one doesn't exist yet."""
        if self.session_id is None and self.client:
            Logger.info("Creating chat session...")
            self._call_callback("on_status","Creating chat session...")
            self.session_id = self.client.create_chat()

            if not self.session_id:
                Logger.error("Failed to create chat session - no session ID returned")
                return False

            Logger.success(f"Chat session created: {self.session_id}")
            # Initialize chat cache for this session
            self.chat_cache = ChatCache(
                chat_id=self.session_id,
                model_name=self.model,
                thinking_mode=(self.mode == "thinking"),
            )
        return True
    
    def register_callbacks(self, callbacks: Dict[str, Callable]):
        """
        Register callbacks for UI updates.
        
        Expected callbacks:
        - on_message: Called when agent sends a message
        - on_tool: Called when agent executes a tool
        - on_status: Called when agent status changes
        - on_question: Called when agent asks a question
        - on_finish: Called when agent finishes
        - on_error: Called on error
        - on_thinking_title: Called when thinking title is updated (in thinking mode)
        - on_tool_permission: Called when tool permission is needed
        """
        self.callbacks = callbacks
    
    def attach_file(self, file_path: str) -> dict:
        """
        Upload an image and queue it as a pending file attachment.

        Extracts fileId from the upload response and stores it in
        pending_file_ids for use in send_prompt's file_ids parameter.

        Returns the file object dict (file.json structure) on success.
        Raises on failure.
        """
        if not self.client:
            raise RuntimeError("Agent not initialized")
        file_obj = self.client.upload_image(file_path)
        self.pending_files.append(file_obj)
        # Extract file IDs from the upload response {"fileId": [id1, id2, ...]}
        file_ids = file_obj.get('fileId', [])
        if isinstance(file_ids, list):
            self.pending_file_ids.extend(file_ids)
        elif isinstance(file_ids, str):
            self.pending_file_ids.append(file_ids)
        Logger.info(f"File attached: {file_obj.get('name', file_path)} (file_ids={file_ids}, {len(self.pending_files)} pending)")
        return file_obj

    def clear_pending_files(self):
        """Remove all pending file attachments and their IDs."""
        self.pending_files.clear()
        self.pending_file_ids.clear()

    def run(self, user_prompt: str):
        """Run the agent with the given prompt using asyncio."""
        if self.running:
            Logger.warn("Agent is already running")
            return
        
        if not self.client:
            Logger.error("Agent not initialized")
            return
        
        self.running = True
        # Create asyncio task instead of thread
        self._task = asyncio.create_task(self._run_agent_task(user_prompt))
    
    async def _run_agent_task(self, user_prompt: str):
        """Run the agent as an async task."""
        try:
            # Ensure chat session exists before sending first prompt
            if not await asyncio.to_thread(self._ensure_chat_session):
                Logger.error("Failed to create chat session")
                self._call_callback('on_error', 'Failed to create chat session')
                return
            # Run the agent loop
            await self._run_agent_loop(user_prompt)
            
        except asyncio.CancelledError:
            self.client.StopSendingPrompt(self.session_id, self.previous_message_id)
            self.client.StopSendingPrompt(self.session_id, self.parent_message_id)
            Logger.info("Agent task cancelled")
        except Exception as e:
            Logger.error(f"Agent task error: {e}")
            Logger.error(traceback.format_exc())
            self._call_callback('on_error', str(e))
        finally:
            self.running = False
            self._call_callback('on_status', 'finished')
    
    async def _run_agent_loop(self, initial_prompt: str):
        """Main agent loop - async version using asyncio for concurrency."""
        prompt = initial_prompt
        pending_token = None  # Solved captcha token to attach to the next request
        while True:
            try:

                # await asyncio.sleep(1)
                # Send prompt to API using asyncio.to_thread to avoid blocking
                if prompt!=initial_prompt:
                    # self._call_callback('on_status', 'Sending Results .....')
                    Logger.info(f"{prompt}")
                    
                self._call_callback('on_status', 'running')
                
                # Check if plan mode is enabled and prepend a reminder to the prompt
                from app.agent.module import PLAN_MODE_ENABLED as _PLAN_MODE
                if _PLAN_MODE:
                    plan_mode_prefix = "[PLAN MODE ACTIVE] You are currently in read-only plan mode. Focus on research, analysis, and planning. Do not request write operations (write_file, replace, run_shell_command that modifies state) until you exit plan mode by calling enter_plan_mode with plan=false.\n"
                    prompt_to_send = plan_mode_prefix + prompt
                else:
                    prompt_to_send = prompt
                
                # Collect pending file IDs for the initial prompt only
                file_ids_to_send = None
                if prompt == initial_prompt and self.pending_file_ids:
                    file_ids_to_send = list(self.pending_file_ids)
                    self.pending_files.clear()
                    self.pending_file_ids.clear()
                    Logger.info(f"Sending {len(file_ids_to_send)} file id(s) with prompt")

                response = await asyncio.to_thread(
                    self.client.send_prompt,
                    prompt_to_send,
                    self.session_id,
                    self.parent_message_id,
                    file_ids_to_send,
                )
                if isinstance(response, dict) and response.get("error") is not None:
                    if response.get("error") == "captcha":
                        captcha_url = response.get("url", "")
                        Logger.warn("Captcha detected, solving automatically...")
                        captcha_progress = self._make_captcha_progress_callback()
                        self._call_callback('on_captcha_progress', "Security check detected. Solving the captcha for you...")
                        self._call_callback('on_status', 'Solving captcha...')
                        try:
                            captcha_new_token = await asyncio.to_thread(
                                self._solve_captcha_via_api,
                                captcha_url,
                                captcha_progress,
                            )
                            if captcha_new_token:
                                Logger.success("Captcha solved. Active token updated with new token. Retrying prompt...")
                                self._call_callback('on_captcha_progress', "Captcha solved. Resuming your request...")
                                self._call_callback('on_status', 'running')
                                await asyncio.sleep(1)
                                pending_token = None
                                continue
                            else:
                                Logger.error("Captcha solver failed to obtain a new token")
                                self._call_callback('on_error', "Captcha could not be solved automatically. Please try again.")
                                return
                        except Exception as captcha_err:
                            Logger.error(f"Captcha solver error: {captcha_err}")
                            Logger.error(traceback.format_exc())
                            self._call_callback('on_error', f"Captcha solver error: {captcha_err}")
                            return
                    elif response.get("error") == "RateLimited":
                        # Daily usage limit reached - rotate token and migrate chats to the next account
                        wait_hours = response.get("num")
                        Logger.warn("Daily usage limit reached on current account. Rotating token...")
                        self._call_callback('on_error_display', "Daily usage limit reached. Migrating chats to next account...")
                        rotated = await self._rotate_token_on_rate_limit()
                        if rotated:
                            Logger.success("Token rotated and chats migrated. Retrying prompt...")
                            self._call_callback('on_status', 'running')
                            await asyncio.sleep(1)
                            continue
                        else:
                            wait_msg = (
                                f"Daily usage limit reached. Please wait {wait_hours} hours before trying again."
                                if wait_hours else "Daily usage limit reached. Try again later."
                            )
                            self._call_callback('on_error', wait_msg)
                            return
                    elif "429" in str(response.get("error", "")) or "rate limit" in str(response.get("error", "")).lower():
                        # HTTP 429 Rate limit from proxy - retry with backoff
                        if not hasattr(self, '_rate_limit_retries'):
                            self._rate_limit_retries = 0

                        if self._rate_limit_retries < 5:
                            wait_time = (self._rate_limit_retries + 1) * 5
                            Logger.warn(f"Rate limited (HTTP 429). Retry {self._rate_limit_retries + 1}/3 in {wait_time}s...")
                            self._call_callback('on_error_warnning', f"Rate limited. Retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            self._rate_limit_retries += 1
                            continue
                        else:
                            # Exhausted retries - report error
                            self._rate_limit_retries = 0
                            Logger.error("Rate limited (HTTP 429). All retries exhausted.")
                            self._call_callback('on_error', "Rate limit exceeded. All retries exhausted. Try again later.")
                            return
                    else:
                        self._call_callback('on_error', f"Failed to send prompt: {response}")
                        return
                file_ids_to_send = None  # Reset after sending
                pending_token = None  # Reset after sending
                # Reset rate-limit retry counter on successful send
                if hasattr(self, '_rate_limit_retries'):
                    self._rate_limit_retries = 0

                # Fetch and cache the chat title once per session (after first prompt is sent)
                if prompt == initial_prompt and not self._title_fetched:
                    self._title_fetched = True
                    Logger.info(f"[TitleFetch] Fetching title for session: {self.session_id}, chat_cache: {self.chat_cache is not None}")
                    try:
                        await asyncio.sleep(3)
                        title = await asyncio.to_thread(
                            self.client.get_chat_title, self.session_id
                        )
                        Logger.info(f"[TitleFetch] Response title: {title}")
                        if title and title != "New chat" and self.chat_cache:
                            self.chat_cache.set_title(title)
                        elif not title or title == "New chat":
                            self._title_fetched = False
                    except Exception as title_err:
                        Logger.warn(f"Failed to fetch chat title: {title_err}")
                        self._title_fetched = False

                # self._call_callback('on_status', 'Sent ......')
            except Exception as e:
                Logger.error(f"Failed to send prompt: {e}")
                Logger.error(f"Prompt: {prompt_to_send}")
                Logger.error(traceback.format_exc())
                self._call_callback('on_error', f"Failed to send prompt: {e}")
                return
            
            status = None
            results = []

            # --- Chat Cache: cache the prompt being sent as a user message ---
            if self.chat_cache:
                self.chat_cache.reset_turn()
                # User message: no own message_id; parent is the last assistant response_id
                self.chat_cache.cache_user_message(
                    prompt,
                    message_id=None,
                    parent_message_id=self.parent_message_id,
                )

            # Stream events one-by-one from a background thread via asyncio.Queue
            queue: asyncio.Queue = asyncio.Queue()
            lastEvent=None
            def _stream_worker():
                nonlocal lastEvent
                try:
                    # The proxy backend emits pre-parsed high-level SSE events
                    # (docs/API.md); the direct backend emits the raw Darktech
                    # stream that needs full client-side parsing.
                    events_iter = parse_proxy_stream(response)

                    for evt in events_iter:
                        queue.put_nowait(evt)
                        lastEvent=evt
                except Exception as stream_err:
                    Logger.error(f"Stream worker error: {stream_err}")
                    Logger.error(traceback.format_exc())
                    queue.put_nowait({'type': 'error', 'value': str(stream_err)})
                finally:
                    if lastEvent is None or lastEvent.get("type") != "closed":
                        Logger.warn("Stream ended without 'closed' event")
                        Logger.warn(str(lastEvent))
                    queue.put_nowait(None)
            
            import threading as _threading
            _stream_thread = _threading.Thread(target=_stream_worker, daemon=True)
            _stream_thread.start()
            
            tool_received = False
            timeout_retry = False
            quota_retry = False
            rate_limited_retry = False
            tool_actions = []  # Track tool call actions for caching
            while True:
                event = await queue.get()
                if event is None:
                    _stream_thread.join(timeout=1)
                    break

                try:
                    evt_type = event.get('type')

                    if evt_type=="finished":
                        break
                    
                    if evt_type == 'error':
                        code = event.get('code')


                        if code == 'Timeout':
                            # Stream timeout: display error in red (without enabling input) and retry with previous parent_message_id
                            Logger.warn(f"Stream timeout detected, stopping completion and retrying with previous parent_message_id")
                            self._call_callback('on_error_display', f"{event.get('value')}")
                            self._call_callback('on_error_display', "Retrying.......")
                            self.parent_message_id = self.previous_message_id
                            await asyncio.sleep(2)
                            timeout_retry = True
                            break
                        elif code=="invalid_input":
                            # Stream timeout: display error in red (without enabling input) and retry with previous parent_message_id
                            Logger.warn(f"{event.get('value')}")
                            self._call_callback('on_error_display', f"{event.get('value')}")
                            self._call_callback('on_error_display', "Retrying.......")
                            self.parent_message_id = self.previous_message_id
                            await asyncio.sleep(2)
                            timeout_retry = True
                            break
                        elif code=="internal_error":
                            # Stream timeout: display error in red (without enabling input) and retry with previous parent_message_id
                            Logger.warn(f"{event.get('value')}")
                            self._call_callback('on_error_display', f"{event.get('value')}")
                            self._call_callback('on_error_display', "Retrying.......")
                            self.parent_message_id = self.previous_message_id
                            await asyncio.sleep(2)
                            timeout_retry = True
                            break
                        elif code == 'quota_limit':
                            # Quota limit: display error in red (without enabling input), wait 5s, then retry
                            Logger.warn(f"Quota limit reached: {event.get('value')}. Retrying in 5 seconds...")
                            self._call_callback('on_error_display', f"{event.get('value')} (retrying in 5s...)")

                            self.parent_message_id = self.previous_message_id
                            await asyncio.sleep(5)
                            quota_retry = True
                            break
                        
                        elif code == 'Bad_Request':
                            continue
                        elif code == 'RateLimited':
                            Logger.warn(f"Rate limited on current account.")
                            self._call_callback('on_error_display', "Rate limited on this account.")
                            
                            rate_limited_retry = True
                            break
                        else:
                            self._call_callback('on_error', f"{event.get('value')}")
                            return
                    elif evt_type=="warnning":
                        code = event.get('code')
                        if code == 'Timeout':
                            # Stream timeout: display error in red (without enabling input) and retry with previous parent_message_id
                            Logger.warn(f"Stream timeout detected, stopping completion and retrying with previous parent_message_id")
                            self._call_callback('on_error_warnning', f"{event.get('value')}")
                            continue
                    
                    elif evt_type == 'tokens':
                        self.total_tokens = event['value']['total_tokens']
                        self._call_callback('on_status', {'tokens': self.total_tokens})
                        # Capture the full usage object for the cached assistant message
                        if self.chat_cache:
                            self.chat_cache.set_usage(event['value'])
                        continue
                    
                    elif evt_type == 'response_id':
                        self.previous_message_id=self.parent_message_id
                        self.parent_message_id = event['value']
                        # Update chat cache message_id (response_id)
                        if self.chat_cache:
                            self.chat_cache.set_response_id(event['value'])
                        continue

                    elif evt_type == 'parent_message_id':
                        # API explicitly sends the parent_message_id for this response
                        if self.chat_cache:
                            self.chat_cache.set_parent_message_id(event['value'])
                            # The assistant's parent is the user message just sent;
                            # backfill its server-assigned id (null until now).
                            self.chat_cache.backfill_user_message_id(event['value'])
                        continue

                    elif evt_type == 'status':
                        # self._call_callback('on_thinking_title', '')
                        status = event['value']
                        if status=='finished' or status=='waiting':
                            continue
                        self._call_callback('on_status', status)
                        continue
                    
                    elif evt_type == 'tool':
                        self._call_callback('on_thinking_title', '')
                        tool_received = True
                        Logger.info(f"Tool received: {tool_received}")
                        tool_data = event['value']
                        tool = tool_data.get('tool')
                        args = tool_data.get('arguments', {})

                        if not tool:
                            continue

                        # Track tool action for chat cache (skip terminal tools)
                        if tool not in ('user_response', 'ask_user'):
                            tool_actions.append(tool_data)

                        if tool == 'read_file' and args.get('path', '').replace('\\', '/').endswith('tool_output.txt'):
                            prompt="The tools output is in this uploaded file 'tool_output.txt'"
                            self.parent_message_id=self.previous_message_id
                            continue
                        
                        # Handle user_response tool specially
                        if tool == 'user_response':
                            # Text was already streamed via stream_text events;
                            # finalize the streaming widget without duplicating text.
                            self._call_callback('on_finish', '')
                            self._call_callback('on_status', status)
                            # Cache assistant message with the final JSON
                            if self.chat_cache:
                                final_json = {"status": status or "finished", "actions": [tool_data]}
                                self.chat_cache.cache_assistant_message(final_json=final_json)
                            return

                        # Handle ask_user tool
                        if tool == 'ask_user':
                            # Text was already streamed via stream_text events;
                            # finalize the streaming widget without duplicating text.
                            self._call_callback('on_finish', '')
                            self._call_callback('on_status', 'finished')
                            # Cache assistant message with the final JSON
                            if self.chat_cache:
                                final_json = {"status": "waiting", "actions": [tool_data]}
                                self.chat_cache.cache_assistant_message(final_json=final_json)
                            return
                        
                        # Notify the UI that a tool call is about to run (renders a "running" card)
                        self._call_callback('on_tool_start', {'tool': tool, 'arguments': args})

                        # Execute tool based on permission mode
                        if self.permission_mode == 'auto':
                            Logger.tool(tool, args)
                            result = await asyncio.to_thread(execute_tool, tool, args)
                            Logger.tool_result(tool, result)
                            self._call_callback('on_tool', {'tool': tool, 'arguments': args, 'status': result.get('status', 'error'), 'result': result.get('result', {})})
                            results.append(result)
                        elif tool not in self._get_allowed_tools():
                            self._call_callback('on_tool_permission', {'tool': tool, 'arguments': args})
                            permission = await self._wait_for_tool_permission()
                            
                            valid_permissions = ['allow_once', 'allow_session', 'deny']
                            if permission not in valid_permissions:
                                Logger.warn(f"Invalid permission value '{permission}', defaulting to 'deny'")
                                permission = 'deny'
                            
                            if permission == 'allow_once':
                                Logger.tool(tool, args)
                                result = await asyncio.to_thread(execute_tool, tool, args)
                                Logger.tool_result(tool, result)
                                self._call_callback('on_tool', {'tool': tool, 'arguments': args, 'status': result.get('status', 'error'), 'result': result.get('result', {})})
                                results.append(result)
                            elif permission == 'allow_session':
                                self._add_allowed_tool(tool)
                                Logger.tool(tool, args)
                                result = await asyncio.to_thread(execute_tool, tool, args)
                                Logger.tool_result(tool, result)
                                self._call_callback('on_tool', {'tool': tool, 'arguments': args, 'status': result.get('status', 'error'), 'result': result.get('result', {})})
                                results.append(result)
                            elif permission == 'deny':
                                self._call_callback('on_tool', {'tool': tool, 'arguments': args, 'denied': True, 'status': 'error'})
                                result = {'error': 'Tool execution denied by user'}
                                results.append(result)
                        else:
                            Logger.tool(tool, args)
                            result = await asyncio.to_thread(execute_tool, tool, args)
                            Logger.tool_result(tool, result)
                            self._call_callback('on_tool', {'tool': tool, 'arguments': args, 'status': result.get('status', 'error'), 'result': result.get('result', {})})
                            results.append(result)
                        continue
                    
                    
                    
                    elif evt_type == 'stream_text':
                        self._call_callback('on_stream_text', event.get('value'))
                        if self.chat_cache:
                            self.chat_cache.add_stream_text(event.get('value'))
                        continue

                    elif evt_type == 'summary_title':
                        self._call_callback('on_thinking_title', event.get('value'))
                        if self.chat_cache:
                            self.chat_cache.add_summary_title(event.get('value'))
                        continue

                    elif evt_type == 'summary_thought':
                        if self.chat_cache:
                            self.chat_cache.add_summary_thought(event.get('value'))
                        continue

                    elif evt_type == 'reasoning':
                        continue
                    
                except Exception as ex:
                    Logger.error(traceback.format_exc())

            # Upload all collected tool results as a single file
            if tool_received and results:
                prompt = f"{json.dumps(results)}"
                # Cache assistant message with the tool call actions (status running + actions)
                if self.chat_cache:
                    assistant_content = {"status": "running", "actions": tool_actions}
                    self.chat_cache.cache_assistant_message(final_json=assistant_content)

            if rate_limited_retry:
                Logger.warn("Rate limited on current account. Rotating token...")
                self._call_callback('on_error_display', "Rate limited on this account. Migrating to next account...")
                rotated = await self._rotate_token_on_rate_limit()
                if rotated:
                    Logger.success("Token rotated and chats migrated. Resending prompt...")
                    self._call_callback('on_status', 'running')
                    await asyncio.sleep(1)
                    continue
                else:
                    self._call_callback('on_error', "Rate limit exceeded on this account. Try again later.")
                    return

            if timeout_retry or quota_retry:
                # Resend the same prompt with the restored previous parent_message_id
                Logger.warn(f"timeout : {timeout_retry} ---- quota : {quota_retry}")
                continue
            
            if not tool_received:
                Logger.warn(f"{self.parent_message_id} - {self.previous_message_id} ==> this message isn't good\nStatus: {status} - Tool Rec: {tool_received} - \nPrompt: {prompt}")
                self.parent_message_id=self.previous_message_id
                
                if json.dumps(EXPECTED_TOOLS) not in prompt:
                    prompt+= f".\nyou should request tool from these \n{json.dumps(EXPECTED_TOOLS)}"
                continue

    
    async def _wait_for_response(self) -> Optional[str]:
        """Wait asynchronously for the user to provide a response to the agent's question."""
        self.response_event.clear()
        self.pending_response = None
        
        # Wait for response asynchronously (with timeout)
        try:
            await asyncio.wait_for(self.response_event.wait(), timeout=self.response_timeout)
        except asyncio.TimeoutError:
            self._call_callback('on_error', f'Timeout waiting for user response ({self.response_timeout}s)')
            return None
        
        return self.pending_response
    
    def provide_response(self, response: str):
        """Provide a response to the agent's question from the UI."""
        self.pending_response = response
        self.response_event.set()
    
    async def _wait_for_tool_permission(self) -> Optional[str]:
        """Wait asynchronously for the user to provide permission for tool execution."""
        self.tool_permission_event.clear()
        self.pending_tool_permission = None
        
        # Wait for permission asynchronously (with timeout)
        try:
            await asyncio.wait_for(self.tool_permission_event.wait(), timeout=self.response_timeout)
        except asyncio.TimeoutError:
            self._call_callback('on_error', f'Timeout waiting for tool permission ({self.response_timeout}s)')
            return 'deny'
        
        return self.pending_tool_permission
    
    def provide_tool_permission(self, permission: str):
        """Provide permission decision for tool execution from the UI."""
        self.pending_tool_permission = permission
        self.tool_permission_event.set()
    
    def _call_callback(self, name: str, data: Any):
        """Call a registered callback safely from async context."""
        if name in self.callbacks:
            callback = self.callbacks[name]
            try:
                # In async context, use call_later to schedule on the event loop
                # This ensures UI updates happen safely from async tasks
                self.app.call_later(callback, data)
            except Exception as e:
                Logger.error(f"Callback error '{name}': {e}")
                Logger.error(traceback.format_exc())

    # Remote captcha solving service (API_DOC.md) — loaded from config/config.json
    @property
    def CAPTCHA_SOLVER_API_BASE(self):
        from app.agent.configData import CAPTCHA_SOLVER_API_BASE
        return CAPTCHA_SOLVER_API_BASE

    def _update_active_token(self, new_token: str):
        """Replace the first (active) token in the tokens list with a new one."""
        try:
            from app.agent.configData import load_tokenfile, save_tokenfile
            token_data = load_tokenfile()
            tokens = token_data.get('tokens', [])
            if isinstance(tokens, list) and tokens:
                tokens[0] = new_token
            else:
                tokens = [new_token]
            token_data['tokens'] = tokens
            token_data.pop('token', None)  # Remove legacy field
            save_tokenfile(token_data)
            # Update the client session to reflect the new token
            Logger.info("Active token updated in tokenfile.json and client session")
        except Exception as e:
            Logger.error(f"Failed to update active token: {e}")

    def _normalize_cache_payload(self, payload: dict) -> dict:
        """Ensure a cache payload satisfies the upload-cache schema.

        The endpoint requires every message to carry a message_id (and a
        parent_message_id, null only for the first message). Messages cached
        before the server revealed their ids may still hold null, so:
        - assign a uuid4 to any message missing a message_id;
        - point any non-first message with a null parent_message_id at the
          previous message (the cache is a linear conversation).
        """
        import uuid
        prev_id = None
        for msg in payload.get('messages', []):
            if not msg.get('message_id'):
                msg['message_id'] = str(uuid.uuid4())
            if prev_id is not None and not msg.get('parent_message_id'):
                msg['parent_message_id'] = prev_id
            prev_id = msg['message_id']
        return payload

    async def _rotate_token_on_rate_limit(self) -> bool:
        """Rotate to the next token when the daily usage limit is hit (README 9.3).

        Steps:
        1. Collect local chat cache payloads (chat_cache/*.json) - already in
           the cache format required by the upload endpoint.
        2. Move the exhausted active token to the end of the tokens list.
        3. Recreate the client so it authenticates with the next token.
        4. Upload the cached chats to the new account via
           POST /api/v1/chats/upload-cache (current session last).
        5. Re-select the migrated chat and restore its parent id.

        Returns True on success, False when rotation is impossible
        (e.g. only one token remains) or an error occurred.
        """
        from app.agent.configData import get_token_count, rotate_first_token
        from app.agent.chat_cache import CACHE_DIR

        token_count = get_token_count()
        if token_count <= 1:
            Logger.error("Rate limited and only one token available - cannot rotate.")
            return False

        try:
            # 1. Collect chat_ids from cache file names ({chat_id}.json)
            chat_ids = []
            if CACHE_DIR.exists():
                for cache_file in sorted(CACHE_DIR.glob("*.json")):
                    chat_id = cache_file.stem  # filename without .json extension
                    if chat_id:
                        chat_ids.append(chat_id)
            # Upload the current session's chat last so it becomes the newest entry
            chat_ids.sort(key=lambda cid: 1 if cid == self.session_id else 0)
            Logger.info(f"Rate-limit rotation: {len(chat_ids)} cached chat(s) to migrate")

            # 2. Move the exhausted token to the end so the next one becomes active
            rotate_first_token()
            Logger.info("Rate-limit rotation: exhausted token moved to end, next account is now active")

            # 3. Recreate the client with the new active token
            old_client = self.client
            self.client = create_client(mode=self.mode, model=self.model)
            try:
                old_client.close()
            except Exception:
                pass

            # 4. Upload cached chats by index: get chat_id -> load its JSON file -> upload
            uploaded = 0
            migrated_id = None
            migrated_parent_id = None
            for index in range(len(chat_ids)):
                chat_id = chat_ids[index]
                cache_file = CACHE_DIR / f"{chat_id}.json"
                try:
                    payload = json.loads(cache_file.read_text(encoding='utf-8'))
                    if not payload.get('chat_id') or not payload.get('messages'):
                        Logger.warn(f"Rate-limit rotation: skipping invalid cache file for chat_id={chat_id}")
                        continue
                    normalized = self._normalize_cache_payload(payload)
                    result = await asyncio.to_thread(
                        self.client.upload_chat_cache,
                        normalized,
                    )
                    uploaded += 1
                    # Track the upload result for the current session's chat
                    if chat_id == self.session_id and isinstance(result, dict):
                        migrated_id = result.get('chat_id')
                        migrated_parent_id = result.get('parent_message_id')
                except (json.JSONDecodeError, IOError) as e:
                    Logger.warn(f"Rate-limit rotation: failed to read cache file for chat_id={chat_id}: {e}")
                except Exception as up_err:
                    Logger.warn(f"Rate-limit rotation: failed to upload chat_id={chat_id}: {up_err}")
            Logger.info(f"Rate-limit rotation: migrated {uploaded}/{len(chat_ids)} chat(s)")

            # 5. Re-select the migrated chat and restore its parent message id
            if migrated_id:
                self.session_id = migrated_id
                if migrated_parent_id:
                    self.parent_message_id = migrated_parent_id
                    self.previous_message_id = migrated_parent_id
                else:
                    # Fallback: fetch parent id from server
                    try:
                        new_parent = await asyncio.to_thread(
                            self.client.get_chat_parent_Id, self.session_id
                        )
                        if new_parent:
                            self.parent_message_id = new_parent
                            self.previous_message_id = new_parent
                    except Exception as pid_err:
                        Logger.warn(f"Rate-limit rotation: could not fetch new parent id: {pid_err}")
                # Rebind the chat cache to the migrated session
                if self.chat_cache and self.chat_cache.chat_id != self.session_id:
                    self.chat_cache = ChatCache(
                        chat_id=self.session_id,
                        model_name=self.model,
                        thinking_mode=(self.mode == "thinking"),
                    )
                Logger.success(f"Rate-limit rotation: re-selected migrated chat {self.session_id}")
            else:
                Logger.warn("Rate-limit rotation: migrated chat not found in upload results")

            Logger.success("Rate-limit rotation complete.")
            return True
        except Exception as e:
            Logger.error(f"Rate-limit rotation failed: {e}")
            Logger.error(traceback.format_exc())
            return False

    def _solve_captcha_via_api(self, captcha_url: str, progress_callback=None) -> str | None:
        """Solve the Darktech AWSC captcha via the remote Captcha Solver API.

        Sends the active token as 'encrypted_token' via POST /solve, streams
        SSE progress events, captures the new token from the 'encrypted_token'
        SSE event on success, updates the stored token, and returns the new
        token string or None on failure.

        Args:
            captcha_url: The punish URL that triggered the captcha (may be empty,
                         in which case the API triggers a captcha automatically).
            progress_callback: Optional callable(str) invoked with progress messages.
        """
        from app.agent.configData import get_active_token

        token = get_active_token()
        if not token:
            Logger.error("Captcha API solve failed: no active token available")
            if progress_callback:
                progress_callback("No token available for captcha solving.")
            return None

        # Build POST body - send token directly as encrypted_token
        body = {"encrypted_token": token}
        if captcha_url:
            body["captcha_url"] = captcha_url

        def _report(message: str):
            Logger.info(f"Captcha API: {message}")
            if progress_callback:
                progress_callback(message)

        try:
            resp = requests.post(
                f"{self.CAPTCHA_SOLVER_API_BASE}/solve",
                json=body,
                stream=True,
                timeout=180,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            Logger.error(f"Captcha API request failed: {e}")
            _report(f"Captcha solver API unreachable: {e}")
            return None

        new_token = None
        current_event = None

        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    raw_data = line[5:].strip()
                    try:
                        data = json.loads(raw_data)
                    except json.JSONDecodeError:
                        data = raw_data

                    if current_event == "encrypted_token":
                        # The new token returned by the solver
                        new_token = data if isinstance(data, str) else str(data)
                    elif current_event == "log":
                        msg = data.get("message", "") if isinstance(data, dict) else str(data)
                        _report(msg)
                    elif current_event == "error":
                        error_message = data.get("message", "Unknown captcha solver error") if isinstance(data, dict) else str(data)
                        Logger.error(f"Captcha API error: {error_message}")
                        _report(f"Captcha solver error: {error_message}")
                    elif current_event == "done":
                        status = data.get("status") if isinstance(data, dict) else None
                        if status == "success" and new_token:
                            # Update the stored token with the new one
                            self._update_active_token(new_token)
                            Logger.success("Captcha solved: active token updated with new token from solver")
                            return new_token
                        return None
        except Exception as e:
            Logger.error(f"Captcha API stream read error: {e}")
            _report(f"Captcha solver stream error: {e}")
            return None
        finally:
            resp.close()

        # Stream ended without a terminal 'done' event
        if new_token:
            self._update_active_token(new_token)
            return new_token
        Logger.error("Captcha API stream ended without a terminal event")
        return None

    def _make_captcha_progress_callback(self):
        """Build a thread-safe progress callback for the captcha solver.

        The solver runs in a worker thread (via asyncio.to_thread), so UI
        updates are marshalled back onto the app event loop.
        """
        def progress(message: str):
            try:
                self.app.call_from_thread(self._handle_captcha_progress, message)
            except Exception:
                Logger.info(f"Captcha progress: {message}")
        return progress

    def _handle_captcha_progress(self, message: str):
        """Write a captcha progress line into the conversation (runs on the event loop)."""
        callback = self.callbacks.get('on_captcha_progress')
        if callback:
            callback(message)

    def cancel(self):
        """Cancel the running agent task."""
        if self.running:
            # Cancel the asyncio task
            if self._task and not self._task.done():
                self._task.cancel()
            self.running = False
            Logger.info("Agent cancelled")
    
    def update_model(self, model_name: str):
        """Update the model used by the agent, recreating the client with the new model while preserving mode."""
        self.client = create_client(mode=self.mode, model=model_name)
        self.model=model_name
        # Update chat cache model tracking
        if self.chat_cache:
            self.chat_cache.model_name = model_name
        Logger.info(f"Agent model updated to: {model_name} (client recreated)")
        return True

    def toggle_mode(self):
        """Toggle between normal and thinking mode, recreating the client with the new mode."""
        self.mode = "thinking" if self.mode == "normal" else "normal"
        self.client = create_client(mode=self.mode,model=self.model)
        # Update chat cache thinking mode tracking
        if self.chat_cache:
            self.chat_cache.thinking_mode = (self.mode == "thinking")
        Logger.info(f"Agent mode toggled to: {self.mode}")
        return self.mode
