import json
import time
import subprocess
import os
import shutil
import shlex
import glob as glob_module
import re
import threading
import uuid
import sys
import datetime
import hashlib
import hmac
import itertools
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Generator, Dict, Any, Optional
import requests
from app.agent.proxy_client import ProxyClient
from app.agent.logger import Logger
from app.agent.edit_file import edit_file as _edit_file

# Global working directory that can be updated dynamically
CURRENT_PATH = os.getcwd()

if getattr(sys, 'frozen', False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).parent.parent.parent

# Path to the CA certificate used for request interception
# _CERT_PATH = str(_BASE / "cert.pem")
_CERT_PATH = None

# Background process tracking
BACKGROUND_PROCESSES = {}
PLAN_MODE_ENABLED = False
_plan_mode_lock = threading.Lock()

# Cached workspace path to avoid repeated os.path.abspath calls
_CACHED_WORKSPACE_PATH = None

def json_size_kb(results):
    return len(json.dumps(results).encode('utf-8')) / 1024

class _DDGParser(HTMLParser):
    """Reusable DuckDuckGo HTML parser. Defined at module level to avoid
    redefining the class on every google_web_search call."""
    def __init__(self):
        super().__init__()
        self.results = []
        self.in_result = False
        self.current = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'a' and 'result__a' in attrs_dict.get('class', ''):
            self.in_result = True
            self.current = {'url': attrs_dict.get('href', ''), 'title': '', 'snippet': ''}

    def handle_data(self, data):
        if self.in_result and self.current:
            self.current['title'] += data.strip()

    def handle_endtag(self, tag):
        if tag == 'a' and self.in_result:
            self.in_result = False
            if self.current.get('url'):
                self.results.append(self.current)

def _get_workspace_path():
    """Get cached absolute workspace path. Recomputes only when CURRENT_PATH changes."""
    global _CACHED_WORKSPACE_PATH
    if _CACHED_WORKSPACE_PATH is None or _CACHED_WORKSPACE_PATH[0] != CURRENT_PATH:
        _CACHED_WORKSPACE_PATH = (CURRENT_PATH, os.path.abspath(CURRENT_PATH))
    return _CACHED_WORKSPACE_PATH[1]

# Pre-compiled regex for grep_search to avoid recompilation
_GREP_PATTERN_CACHE = {}
_MAX_GREP_CACHE_SIZE = 32

def _get_compiled_pattern(pattern_str):
    """Get a compiled regex pattern from cache, compiling if necessary."""
    if pattern_str in _GREP_PATTERN_CACHE:
        return _GREP_PATTERN_CACHE[pattern_str]
    compiled = re.compile(pattern_str)
    # Evict oldest entries if cache is full
    if len(_GREP_PATTERN_CACHE) >= _MAX_GREP_CACHE_SIZE:
        oldest_key = next(iter(_GREP_PATTERN_CACHE))
        del _GREP_PATTERN_CACHE[oldest_key]
    _GREP_PATTERN_CACHE[pattern_str] = compiled
    return compiled

# Cache for shutil.which results to avoid repeated filesystem lookups
_WHICH_CACHE = {}
_state_lock = threading.Lock()

def _which_cached(cmd):
    """Cached version of shutil.which to avoid repeated PATH lookups (thread-safe)."""
    with _state_lock:
        if cmd not in _WHICH_CACHE:
            _WHICH_CACHE[cmd] = shutil.which(cmd)
        return _WHICH_CACHE[cmd]


# ---------------------------------------------------------------------------
# File upload (via the Darktech Proxy REST API, docs/API.md)
# ---------------------------------------------------------------------------

def upload_file_content(content: str, filename: str = 'tool_output.txt') -> dict:
    """Upload content as a file via the Darktech Proxy (POST /api/v1/files/upload)
    and return the file object for use in send_prompt files arrays."""
    client = ProxyClient()
    try:
        return client.upload_file_content(content, filename)
    finally:
        client.close()


def _resolve_cwd(cwd: str) -> str:
    """Resolve a cwd argument to an absolute path relative to CURRENT_PATH."""
    if os.path.isabs(cwd):
        return cwd
    return os.path.abspath(os.path.join(CURRENT_PATH, cwd))


def _parse_command(command: str):
    """Parse a command string into (args_list, first_command).
    Falls back to whitespace splitting if shlex fails."""
    try:
        parsed = shlex.split(command, posix=True)
        if not parsed:
            return [], ""
        return parsed, parsed[0]
    except ValueError:
        parts = command.split()
        if not parts:
            return [], ""
        return parts, parts[0]


def _execute_subprocess(command: str, parsed_cmd, first_cmd: str, cwd: str, timeout):
    """Execute via native binary if available, otherwise via PowerShell."""
    if _which_cached(first_cmd):
        return subprocess.run(
            parsed_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    return subprocess.run(
        ["powershell", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


def _run_background_process(proc_id: str, command: str, parsed_cmd, first_cmd: str, cwd: str, timeout):
    """Execute a background process with streaming output.

    Uses subprocess.Popen to read output incrementally as it is produced,
    updating the shared output buffer so that read_background_output can
    return partial results while the process is still running.

    A separate watchdog timer ensures the process is killed even if it
    produces no output (fixes the per-line-only timeout check bug).

    The thread releases all resources (process handles, pipes) upon completion.
    """
    proc = None
    timed_out = False
    timeout_timer = None

    def _timeout_watchdog():
        """Watchdog callback: kill the process if still running after timeout."""
        nonlocal timed_out
        if proc is not None and proc.poll() is None:
            timed_out = True
            proc.kill()

    try:
        # Use Popen for streaming output (merge stderr into stdout)
        if _which_cached(first_cmd):
            proc = subprocess.Popen(
                parsed_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
            )
        else:
            proc = subprocess.Popen(
                ["powershell", "-Command", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
            )

        # Store the process object for external status checking / kill
        with _state_lock:
            if proc_id in BACKGROUND_PROCESSES:
                BACKGROUND_PROCESSES[proc_id]["process"] = proc

        # Stream output line by line into the shared buffer
        output_lines = []

        # Set up a watchdog timer to kill the process even if it produces no output
        if timeout:
            timeout_timer = threading.Timer(timeout, _timeout_watchdog)
            timeout_timer.daemon = True
            timeout_timer.start()

        for line in proc.stdout:
            output_lines.append(line)
            # Update the shared output buffer so readers can see partial output
            with _state_lock:
                if proc_id in BACKGROUND_PROCESSES:
                    BACKGROUND_PROCESSES[proc_id]["output"] = "".join(output_lines)

        # Cancel the watchdog timer if the process finished naturally
        if timeout_timer:
            timeout_timer.cancel()

        # Wait for process to fully exit and get return code
        proc.wait()
        returncode = proc.returncode

        with _state_lock:
            if proc_id in BACKGROUND_PROCESSES:
                if timed_out:
                    BACKGROUND_PROCESSES[proc_id]["output"] = "".join(output_lines) + f"\nProcess timed out after {timeout}s"
                    BACKGROUND_PROCESSES[proc_id]["status"] = "timeout"
                else:
                    BACKGROUND_PROCESSES[proc_id]["output"] = "".join(output_lines)
                    BACKGROUND_PROCESSES[proc_id]["status"] = "completed"
                    BACKGROUND_PROCESSES[proc_id]["returncode"] = returncode

    except Exception as ex:
        if timeout_timer:
            timeout_timer.cancel()
        with _state_lock:
            if proc_id in BACKGROUND_PROCESSES:
                BACKGROUND_PROCESSES[proc_id]["output"] = str(ex)
                BACKGROUND_PROCESSES[proc_id]["status"] = "failed"
    finally:
        # Cancel watchdog timer and release process resources
        if timeout_timer:
            timeout_timer.cancel()
        if proc is not None:
            try:
                if proc.stdout:
                    proc.stdout.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                pass

def _handle_shell_command(arguments: dict):
    """Handle the run_shell_command tool with full validation."""
    command = arguments.get('command')
    if not command:
        return {
            "status": "error",
            "tool": "run_shell_command",
            "result": {"error_msg": "Missing 'command' argument"}
        }

    # Validate timeout
    timeout = arguments.get('timeout')
    if timeout is not None:
        try:
            timeout = int(timeout)
        except (ValueError, TypeError):
            return {
                "status": "error",
                "tool": "run_shell_command",
                "result": {"error_msg": f"Invalid timeout value: {timeout!r}"}
            }
    else:
        timeout = 1800
    

    cwd = arguments.get('cwd', '.')
    resolved_cwd = _resolve_cwd(cwd)

    # Validate working directory exists
    if not os.path.isdir(resolved_cwd):
        return {
            "status": "error",
            "tool": "run_shell_command",
            "result": {"error_msg": f"Working directory does not exist: {resolved_cwd}"}
        }

    parsed_cmd, first_cmd = _parse_command(command)
    if not parsed_cmd:
        return {
            "status": "error",
            "tool": "run_shell_command",
            "result": {"error_msg": "Empty command"}
        }

    # Background execution
    if arguments.get('background', False):
        proc_id = str(uuid.uuid4())[:8]
        with _state_lock:
            BACKGROUND_PROCESSES[proc_id] = {
                "command": command,
                "status": "running",
                "output": "",
                "thread": None,
                "process": None,
                "returncode": None
            }
        t = threading.Thread(
            target=_run_background_process,
            args=(proc_id, command, parsed_cmd, first_cmd, resolved_cwd, None),
            daemon=True,
        )
        with _state_lock:
            BACKGROUND_PROCESSES[proc_id]["thread"] = t
        t.start()
        Logger.info(f"Background process started: id={proc_id}, command={command}")
        return {
            "status": "success",
            "tool": "run_shell_command",
            "command": command,
            "result": {
                "background_id": proc_id,
                "message": f"Process started in background with id {proc_id}"
            }
        }

    # Foreground execution
    try:
        result = _execute_subprocess(command, parsed_cmd, first_cmd, resolved_cwd, timeout)
        return {
            "status": "success",
            "tool": "run_shell_command",
            "command": command,
            "result": {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        }
    except subprocess.TimeoutExpired as e:
        Logger.warn(f"Tool 'run_shell_command' timed out after {timeout}s: command={command}")
        stdout = e.stdout.decode() if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or '')
        stderr = e.stderr.decode() if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or '')
        return {
            "status": "success",
            "tool": "run_shell_command",
            "result": {
                "stdout": stdout,
                "stderr": stderr,
                "returncode": -1,
            }
        }


def _handle_code_interpreter(arguments: dict) -> dict:
    """Handle the code_interpreter tool: execute Python code via python -c."""
    code = arguments.get('code')
    if not code:
        return {
            "status": "error",
            "tool": "code_interpreter",
            "result": {"error_msg": "Missing 'code' argument"}
        }

    timeout = arguments.get('timeout', 60)
    try:
        timeout = int(timeout)
    except (ValueError, TypeError):
        timeout = 60

    try:
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=CURRENT_PATH,
        )
        return {
            "status": "success",
            "tool": "code_interpreter",
            "result": {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        }
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or '')
        stderr = e.stderr.decode() if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or '')
        return {
            "status": "success",
            "tool": "code_interpreter",
            "result": {
                "stdout": stdout,
                "stderr": stderr,
                "returncode": -1,
            }
        }
    except Exception as e:
        Logger.error(f"Tool 'code_interpreter' failed: {e}")
        return {
            "status": "error",
            "tool": "code_interpreter",
            "result": {"error_msg": str(e)}
        }


def _handle_list_background_processes() -> dict:
    """Handle the list_background_processes tool with thread-safe state access.
    
    Returns completed processes on first request, then removes them from
    tracking and joins (closes) their threads after returning.
    """
    processes = []
    threads_to_join = []

    with _state_lock:
        completed_ids = []
        for proc_id, proc_info in list(BACKGROUND_PROCESSES.items()):
            thread = proc_info.get("thread")
            actual_status = proc_info["status"]
            
            # Refresh status from thread state
            if thread is not None:
                if thread.is_alive():
                    actual_status = "running"
                elif actual_status == "running":
                    actual_status = "completed"
                    BACKGROUND_PROCESSES[proc_id]["status"] = "completed"

            # Include in the response (so the user sees it on first request)
            processes.append({
                "id": proc_id,
                "command": proc_info["command"],
                "status": actual_status,
                "source": "tracked"
            })

            # If completed, mark for cleanup AFTER the response is built
            if actual_status == "completed":
                completed_ids.append(proc_id)
                if thread is not None:
                    threads_to_join.append(thread)

        # Remove completed processes from tracking (after including them in response)
        for proc_id in completed_ids:
            del BACKGROUND_PROCESSES[proc_id]

        tracked_count = len(BACKGROUND_PROCESSES)

    # Join (close) completed threads outside the lock
    for t in threads_to_join:
        t.join(timeout=1)

    Logger.info(f"list_background_processes called: {len(processes)} total ({tracked_count} tracked)")
    return {
        "status": "success",
        "tool": "list_background_processes",
        "result": {
            "processes": processes,
            "tracked_count": tracked_count,
        }
    }


def _handle_read_background_output(arguments: dict) -> dict:
    """Handle the read_background_output tool with validation and thread-safe access.
    
    Returns the completed process output on first read, then removes it from
    tracking and joins (closes) its thread after returning.
    """
    proc_id = arguments.get('id')
    if not proc_id:
        return {
            "status": "error",
            "tool": "read_background_output",
            "result": {"error_msg": "Missing 'id' argument"}
        }
    
    thread_to_join = None
    
    with _state_lock:
        if proc_id not in BACKGROUND_PROCESSES:
            return {
                "status": "error",
                "tool": "read_background_output",
                "result": {"error_msg": f"Process {proc_id} not found"}
            }
        
        proc_info = BACKGROUND_PROCESSES[proc_id]
        
        # Refresh status from thread state if needed
        thread = proc_info.get("thread")
        actual_status = proc_info["status"]
        if thread is not None:
            if thread.is_alive():
                actual_status = "running"
            elif actual_status == "running":
                actual_status = "completed"
        
        # Build the result FIRST (so the user receives the data)
        result = {
            "status": "success",
            "tool": "read_background_output",
            "result": {
                "output": proc_info["output"],
                "status": actual_status
            }
        }
        
        # If completed, remove from tracking and schedule thread join AFTER returning
        if actual_status == "completed":
            if thread is not None:
                thread_to_join = thread
            del BACKGROUND_PROCESSES[proc_id]
    
    # Join (close) the thread outside the lock
    if thread_to_join is not None:
        thread_to_join.join(timeout=1)
    
    return result

def _handle_kill_process(arguments: dict) -> dict:
    """Handle the kill_process tool: terminate a background process by its ID."""
    proc_id = arguments.get('id')
    if not proc_id:
        return {
            "status": "error",
            "tool": "kill_process",
            "result": {"error_msg": "Missing 'id' argument"}
        }

    with _state_lock:
        if proc_id not in BACKGROUND_PROCESSES:
            return {
                "status": "error",
                "tool": "kill_process",
                "result": {"error_msg": f"Process {proc_id} not found"}
            }

        proc_info = BACKGROUND_PROCESSES[proc_id]
        process = proc_info.get("process")

        if process is None:
            return {
                "status": "error",
                "tool": "kill_process",
                "result": {"error_msg": f"Process {proc_id} has no associated OS process"}
            }

        # Check if already terminated
        if process.poll() is not None:
            BACKGROUND_PROCESSES[proc_id]["status"] = "completed"
            return {
                "status": "success",
                "tool": "kill_process",
                "result": {
                    "id": proc_id,
                    "message": f"Process {proc_id} was already terminated (exit code: {process.returncode})"
                }
            }

        # Kill the process
        try:
            process.kill()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Force kill if graceful wait times out
            process.kill()
        except Exception as e:
            return {
                "status": "error",
                "tool": "kill_process",
                "result": {"error_msg": f"Failed to kill process {proc_id}: {e}"}
            }

        BACKGROUND_PROCESSES[proc_id]["status"] = "killed"
        Logger.info(f"Process killed: id={proc_id}, command={proc_info['command']}")

    return {
        "status": "success",
        "tool": "kill_process",
        "result": {
            "id": proc_id,
            "message": f"Process {proc_id} has been killed"
        }
    }


def set_working_directory(path):
    """Update the global working directory for tool operations."""
    global CURRENT_PATH, _CACHED_WORKSPACE_PATH
    abs_path = os.path.abspath(path)
    if os.path.isdir(abs_path):
        CURRENT_PATH = abs_path
        # Invalidate workspace path cache
        _CACHED_WORKSPACE_PATH = None
        return True
    return False

def get_working_directory():
    """Get the current working directory for tool operations."""
    return CURRENT_PATH

# All API traffic goes through the Darktech Proxy REST API (docs/API.md).
# DarktechClient is kept as an alias of ProxyClient for backward compatibility.


def create_client(mode="normal", model="darktech_v3"):
    """Creates and configures an agent client.

    Every call is routed through the Darktech Proxy REST API documented in
    docs/API.md (base URL configured via the 'proxy_url' setting).
    """
    client = ProxyClient(model=model, mode=mode)
    Logger.info(f"Agent client using proxy: {client.base_url}")
    return client


EXPECTED_TOOLS=[
  {
    "tool": "current_path",
    "arguments": {}
  },
  {
    "tool": "list_directory",
    "arguments": {
      "path": "string"
    }
  },
  {
    "tool": "glob",
    "arguments": {
      "pattern": "string",
      "path": "string (optional)"
    }
  },
  {
    "tool": "grep_search",
    "arguments": {
      "pattern": "string",
      "path": "string (optional)",
      "include": "string (optional)"
    }
  },
  {
      "tool": "read_file",
      "arguments": {
          "path": "string",
          "start_line": "integer (optional)",
          "end_line": "integer (optional)"
      }
  },
  {
    "tool": "replace",
    "arguments": {
      "path": "string",
      "search": "string",
      "replace": "string"
    }
  },
  {
    "tool": "write_file",
    "arguments": {
      "path": "string",
      "content": "string"
    }
  },
  {
  "tool": "run_shell_command",
  "arguments": {
  "command": "string",
  "cwd": "string (optional)",
  "timeout": "integer (optional)"
  }
  },
  {
  "tool": "code_interpreter",
  "arguments": {
  "code": "string"
  }
  },
  {
    "tool": "list_background_processes",
    "arguments": {}
  },
  {
    "tool": "read_background_output",
    "arguments": {
      "id": "string"
    }
  },
  {
    "tool": "kill_process",
    "arguments": {
      "id": "string"
    }
  },
  {
    "tool": "enter_plan_mode",
    "arguments": {
      "plan": "boolean (true to enter plan mode, false to exit plan mode)"
    }
  },
  {
    "tool": "google_web_search",
    "arguments": {
      "query": "string"
    }
  },
  {
    "tool": "web_fetch",
    "arguments": {
      "url": "string"
    }
  },
  {
    "tool": "ask_user",
    "arguments": {
      "question": "string"
    }
  },
  {
    "tool": "user_response",
    "arguments": {
      "description": "string"
    }
  }
]




# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def execute_tool(name: str, arguments: dict):
    """Executes a tool by name with the provided arguments."""
    # Security: Validate file paths to prevent path traversal
    if name in ('read_file', 'write_file', 'replace', 'list_directory', 'run_shell_command', 'glob', 'grep_search'):
        if 'path' in arguments or 'cwd' in arguments:
            # Use cached workspace path to avoid repeated os.path.abspath
            workspace_path = _get_workspace_path()

            # Determine the target path to validate
            if name == 'run_shell_command':
                target_cwd = arguments.get('cwd', '.')
                target_path = os.path.abspath(os.path.join(CURRENT_PATH, target_cwd))
            else:
                target_path = os.path.abspath(os.path.join(CURRENT_PATH, arguments.get('path', '.')))
            
            # Fast prefix check before expensive commonpath call
            # Normalize both paths with trailing separator for accurate prefix matching
            norm_target = target_path.rstrip(os.sep) + os.sep
            norm_workspace = workspace_path.rstrip(os.sep) + os.sep
            
            if not norm_target.startswith(norm_workspace) and target_path != workspace_path:
                # Double-check with commonpath for edge cases (symlinks, case sensitivity)
                try:
                    common = os.path.commonpath([target_path, workspace_path])
                    if common != workspace_path:
                        error_msg = "Path traversal detected: path must be within workspace directory"
                        Logger.error(f"Tool '{name}' blocked: {error_msg} (path={arguments.get('path', arguments.get('cwd', ''))})")
                        return {
                            "status": "error",
                            "tool": name,
                            "result": {
                                "path": arguments.get('path', arguments.get('cwd', '')),
                                "error_msg": error_msg
                            }
                        }
                except ValueError:
                    error_msg = "Path traversal detected: path must be within workspace directory"
                    Logger.error(f"Tool '{name}' blocked: {error_msg} (path={arguments.get('path', arguments.get('cwd', ''))})")
                    return {
                        "status": "error",
                        "tool": name,
                        "result": {
                            "path": arguments.get('path', arguments.get('cwd', '')),
                            "error_msg": error_msg
                        }
                    }
    
    try:
        if name == "code_interpreter":
            return _handle_code_interpreter(arguments)
        elif name == "run_shell_command":
            return _handle_shell_command(arguments)
                
        elif name=="current_path":
            return {
                    "status": "success",
                    "tool": "current_path",
                    "result": {
                        "path": CURRENT_PATH
                    },
                }

        elif name == "list_directory":
            try:
                target_path = os.path.join(CURRENT_PATH, arguments['path'])
                entries = os.listdir(target_path)
                return {
                    "status": "success",
                    "tool": "list_directory",
                    "path": arguments['path'],
                    "result": {
                        "entries": entries
                    }
                }
            except PermissionError as e:
                Logger.error(f"Tool 'list_directory' failed: Permission denied for path={arguments['path']} ({e})")
                return {
                    "status": "error",
                    "tool": "list_directory",
                    "path": arguments['path'],
                    "result": {
                        "error_msg": f"Permission denied: {e}"
                    }
                }
            except FileNotFoundError as e:
                Logger.error(f"Tool 'list_directory' failed: Directory not found path={arguments['path']} ({e})")
                return {
                    "status": "error",
                    "tool": "list_directory",
                    "path": arguments['path'],
                    "result": {
                        "error_msg": f"Directory not found: {e}"
                    }
                }
        
        elif name=="search_file" or name=="glob":
            try:
                matches = []
                search_path = os.path.join(CURRENT_PATH, arguments.get('path', '.'))
                pattern = arguments['pattern']
                full_pattern = os.path.join(search_path, pattern)
                for match in glob_module.glob(full_pattern, recursive=True):
                    matches.append(os.path.relpath(match, CURRENT_PATH))
                return {
                    "status": "success",
                    "tool": name,
                    "path": arguments.get('path', '.'),
                    "result": {
                        "pattern": pattern,
                        "matches": matches
                    }
                }
            except PermissionError as e:
                Logger.error(f"Tool '{name}' failed: Permission denied for path={arguments.get('path', '.')} ({e})")
                return {
                    "status": "error",
                    "tool": name,
                    "result": {
                        "path": arguments.get('path', '.'),
                        "error_msg": f"Permission denied: {e}"
                    }
                }
            except FileNotFoundError as e:
                Logger.error(f"Tool '{name}' failed: Directory not found path={arguments.get('path', '.')} ({e})")
                return {
                    "status": "error",
                    "tool": name,
                    "result": {
                        "path": arguments.get('path', '.'),
                        "error_msg": f"Directory not found: {e}"
                    }
                }

        elif name=="grep_search":
            try:
                results = []
                search_path = os.path.join(CURRENT_PATH, arguments.get('path', '.'))
                # Use cached compiled pattern to avoid recompilation on repeated searches
                pattern = _get_compiled_pattern(arguments['pattern'])
                include = arguments.get('include', '*')
                # Pre-compute relpath base to avoid repeated string operations
                current_path_norm = CURRENT_PATH
                max_results = 500  # Cap results to prevent memory issues on large codebases

                # If search_path is a file, search it directly instead of globbing
                if os.path.isfile(search_path):
                    try:
                        with open(search_path, 'r', encoding='utf-8', errors='ignore') as f:
                            rel_file = os.path.relpath(search_path, current_path_norm)
                            for line_num, line in enumerate(f, 1):
                                if pattern.search(line):
                                    results.append({
                                        "file": rel_file,
                                        "line": line_num,
                                        "content": line.rstrip()
                                    })
                                    if len(results) >= max_results:
                                        break
                    except (PermissionError, UnicodeDecodeError):
                        pass
                else:
                    full_pattern = os.path.join(search_path, '**', include)
                    for filepath in glob_module.glob(full_pattern, recursive=True):
                        if os.path.isfile(filepath):
                            try:
                                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                                    rel_file = os.path.relpath(filepath, current_path_norm)
                                    for line_num, line in enumerate(f, 1):
                                        if pattern.search(line):
                                            results.append({
                                                "file": rel_file,
                                                "line": line_num,
                                                "content": line.rstrip()
                                            })
                                            if len(results) >= max_results:
                                                break
                            except (PermissionError, UnicodeDecodeError):
                                continue
                        if len(results) >= max_results:
                            break
                return {
                    "status": "success",
                    "tool": "grep_search",
                    "result": {
                        "results": results
                    }
                }
            except Exception as e:
                Logger.error(f"Tool 'grep_search' failed: {e}")
                return {
                    "status": "error",
                    "tool": "grep_search",
                    "result": {
                        "error_msg": str(e)
                    }
                }

        elif name=="list_background_processes":
            return _handle_list_background_processes()

        elif name=="read_background_output":
            return _handle_read_background_output(arguments)

        elif name=="kill_process":
            return _handle_kill_process(arguments)

        elif name=="enter_plan_mode":
            global PLAN_MODE_ENABLED
            plan_value = arguments.get('plan', True)
            # Accept both bool and string representations
            if isinstance(plan_value, str):
                plan_value = plan_value.lower() in ('true', '1', 'yes')
            with _plan_mode_lock:
                PLAN_MODE_ENABLED = bool(plan_value)
            return {
                "status": "success",
                "tool": "enter_plan_mode",
                "result": {
                    "mode": "plan" if PLAN_MODE_ENABLED else "normal",
                    "plan_enabled": PLAN_MODE_ENABLED
                }
            }

        elif name=="google_web_search":
            try:
                query = arguments['query']
                # Use DuckDuckGo HTML search as fallback (no API key needed)
                search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
                resp = requests.get(search_url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'}, verify=_CERT_PATH)
                results = []
                # Simple parsing of DDG HTML results - parser class defined at module level for reuse
                parser = _DDGParser()
                parser.feed(resp.text)
                results = [{'title': r['title'], 'url': r['url'], 'snippet': ''} for r in parser.results[:10]]
                return {
                    "status": "success",
                    "tool": "google_web_search",
                    "result": {
                        "results": results
                    }
                }
            except Exception as e:
                Logger.error(f"Tool 'google_web_search' failed: {e}")
                return {
                    "status": "error",
                    "tool": "google_web_search",
                    "result": {
                        "error_msg": str(e)
                    }
                }

        elif name=="web_fetch":
            try:
                resp = requests.get(arguments['url'], timeout=30, verify=_CERT_PATH)
                return {
                    "status": "success",
                    "tool": "web_fetch",
                    "result": {
                        "content": resp.text,  # Limit content size
                        "status_code": resp.status_code
                    }
                }
            except Exception as e:
                Logger.error(f"Tool 'web_fetch' failed: {e}")
                return {
                    "status": "error",
                    "tool": "web_fetch",
                    "result": {
                        "error_msg": str(e)
                    }
                }

        elif name == "write_file":
            try:
                file_path = Path(os.path.join(CURRENT_PATH, arguments['path']))
                file_path.parent.mkdir(parents=True, exist_ok=True)

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(arguments["content"])

                return {
                    "status": "success",
                    "tool": "write_file",
                    "result": {
                        "path": arguments['path']
                    }
                }
            except PermissionError as e:
                Logger.error(f"Tool 'write_file' failed: Permission denied for path={arguments['path']} ({e})")
                return {
                    "status": "error",
                    "tool": "write_file",
                    "path": arguments['path'],
                    "result": {
                        "error_msg": f"Permission denied: {e}"
                    }
                }
            except OSError as e:
                Logger.error(f"Tool 'write_file' failed: OS error for path={arguments['path']} ({e})")
                return {
                    "status": "error",
                    "tool": "write_file",
                    "path": arguments['path'],
                    "result": {
                        "error_msg": f"OS error writing file: {e}"
                    }
                }

        elif name == "read_file":
            try:
                file_path = os.path.join(CURRENT_PATH, arguments['path'])
                # Validate line range arguments
                start_line = arguments.get('start_line')
                end_line = arguments.get('end_line')
                if start_line is not None:
                    try:
                        start_line = int(start_line)
                    except (ValueError, TypeError):
                        return {
                            "status": "error",
                            "tool": "read_file",
                            "error": {
                                "code": "INVALID_LINE_RANGE",
                                "message": f"start_line must be an integer, got: {start_line!r}"
                            }
                        }
                    if start_line < 1:
                        return {
                            "status": "error",
                            "tool": "read_file",
                            "error": {
                                "code": "INVALID_LINE_RANGE",
                                "message": "start_line must be >= 1."
                            }
                        }
                if end_line is not None:
                    try:
                        end_line = int(end_line)
                    except (ValueError, TypeError):
                        return {
                            "status": "error",
                            "tool": "read_file",
                            "error": {
                                "code": "INVALID_LINE_RANGE",
                                "message": f"end_line must be an integer, got: {end_line!r}"
                            }
                        }
                    if end_line < 1:
                        return {
                            "status": "error",
                            "tool": "read_file",
                            "error": {
                                "code": "INVALID_LINE_RANGE",
                                "message": "end_line must be >= 1."
                            }
                        }
                if start_line is not None and end_line is not None and end_line < start_line:
                    return {
                        "status": "error",
                        "tool": "read_file",
                        "error": {
                            "code": "INVALID_LINE_RANGE",
                            "message": "start_line must be less than or equal to end_line."
                        }
                    }
                # Count total lines with chunked reads (memory-safe) instead of
                # loading the entire file via readlines(). Text mode with universal
                # newlines keeps line semantics identical to readlines() and
                # surfaces UnicodeDecodeError the same way.
                _COUNT_CHUNK = 65536
                newline_count = 0
                last_char = ''
                with open(file_path, encoding="utf-8") as f:
                    while True:
                        chunk = f.read(_COUNT_CHUNK)
                        if not chunk:
                            break
                        newline_count += chunk.count('\n')
                        last_char = chunk[-1]
                total_lines = newline_count + (1 if last_char and last_char != '\n' else 0)
                # Determine effective range
                if start_line is None:
                    start_line = 1
                range_end = min(end_line, total_lines) if end_line is not None else total_lines
                # Clamp start_line to file bounds
                if start_line > total_lines:
                    # Requested range is beyond EOF - return empty content
                    return {
                        "status": "success",
                        "tool": "read_file",
                        "result": {
                            "path": arguments['path'],
                            "content": "",
                            "start_line": start_line,
                            "end_line": start_line,
                            "total_lines": total_lines,
                            "truncated": False
                        }
                    }
                # Stream only the requested lines and stop accumulating once the
                # serialized result would exceed the 20 KB cap. This avoids holding
                # the whole file in memory and removes the repeated json.dumps
                # re-serialization halving loop entirely (single pass instead).
                MAX_RESULT_SIZE_KB = 50
                max_bytes = MAX_RESULT_SIZE_KB * 1024
                # Fixed envelope overhead (result object with empty content)
                base_bytes = len(json.dumps({
                    "status": "success",
                    "tool": "read_file",
                    "result": {
                        "path": arguments['path'],
                        "content": "",
                        "start_line": start_line,
                        "end_line": range_end,
                        "total_lines": total_lines,
                        "truncated": False
                    }
                }).encode('utf-8'))
                selected_lines = []
                used_bytes = base_bytes
                with open(file_path, encoding="utf-8") as f:
                    for line in itertools.islice(f, start_line - 1, range_end):
                        line_bytes = len(json.dumps(line).encode('utf-8'))
                        if selected_lines and used_bytes + line_bytes > max_bytes:
                            break
                        selected_lines.append(line)
                        used_bytes += line_bytes
                effective_end = start_line + len(selected_lines) - 1
                content_str = "".join(selected_lines)
                truncated = effective_end < total_lines

                return {
                    "status": "success",
                    "tool": "read_file",
                    "result": {
                        "path": arguments['path'],
                        "content": content_str,
                        "start_line": start_line,
                        "end_line": effective_end,
                        "total_lines": total_lines,
                        "truncated": truncated
                    }
                }
            except FileNotFoundError:
                Logger.error(f"Tool 'read_file' failed: File not found path={arguments['path']}")
                return {
                    "status": "error",
                    "tool": "read_file",
                    "error": {
                        "code": "FILE_NOT_FOUND",
                        "message": f"File '{arguments['path']}' does not exist."
                    }
                }
            except PermissionError:
                Logger.error(f"Tool 'read_file' failed: Permission denied for path={arguments['path']}")
                return {
                    "status": "error",
                    "tool": "read_file",
                    "error": {
                        "code": "PERMISSION_DENIED",
                        "message": f"Permission denied while reading '{arguments['path']}'."
                    }
                }
            except UnicodeDecodeError:
                Logger.error(f"Tool 'read_file' failed: Invalid UTF-8 for path={arguments['path']}")
                return {
                    "status": "error",
                    "tool": "read_file",
                    "error": {
                        "code": "INVALID_ENCODING",
                        "message": f"File '{arguments['path']}' is not valid UTF-8 text."
                    }
                }
            
        
            
        elif name=="replace":
            edit_result = _edit_file(
                file_path=os.path.join(CURRENT_PATH, arguments['path']),
                search=arguments['search'],
                replace=arguments['replace'],
                operation="replace"
            )
            if edit_result.success:
                result_data = {
                    "replacements_made": 1,
                    "match_type": edit_result.match_type,
                    "confidence": edit_result.confidence
                }
                if edit_result.diff_preview:
                    result_data["diff_preview"] = edit_result.diff_preview
                if edit_result.lines_added > 0:
                    result_data["lines_added"] = edit_result.lines_added
                if edit_result.lines_removed > 0:
                    result_data["lines_removed"] = edit_result.lines_removed
                if edit_result.start_line is not None:
                    result_data["start_line"] = edit_result.start_line
                if edit_result.end_line is not None:
                    result_data["end_line"] = edit_result.end_line
                return {
                    "status": "success",
                    "tool": "replace",
                    "path": arguments['path'],
                    "result": result_data
                }
            else:
                Logger.error(f"Tool 'replace' failed: {edit_result.error} - {edit_result.message} (path={arguments['path']}, match_count={edit_result.match_count})")
                error_result = {
                    "error_msg": edit_result.message or "Target code not found.",
                    "error_type": edit_result.error
                }
                if edit_result.hint:
                    error_result["hint"] = edit_result.hint
                if edit_result.suggested_action:
                    error_result["suggested_action"] = edit_result.suggested_action
                if edit_result.match_count > 0:
                    error_result["match_count"] = edit_result.match_count
                if edit_result.candidates:
                    error_result["candidates"] = edit_result.candidates
                return {
                    "status": "error",
                    "tool": "replace",
                    "path": arguments['path'],
                    "result": error_result
                }
        else:
            Logger.error(f"Tool execution failed: Unknown tool '{name}' with args={arguments}")
            return {
                    "status": "error",
                    "tool": "unknown tool",
                    "result":{
                        "tool":name,
                        "args":arguments
                    }
                    }

    except Exception as e:
        Logger.error(f"Tool '{name}' failed with unexpected exception: {e} (args={arguments})")
        return {
            "status": "error",
            "tool": name,
            "result": {
                "error_msg": str(e)
            }
        }
