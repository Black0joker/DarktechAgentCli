import datetime
import json
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).parent.parent.parent

class Logger:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    BLUE = "\033[38;5;75m"     # #58a6ff
    GREEN = "\033[38;5;78m"    # #3fb950
    YELLOW = "\033[38;5;214m"  # #d29922
    RED = "\033[38;5;203m"     # #f85149
    CYAN = "\033[38;5;141m"    # #bc8cff
    MAGENTA = "\033[38;5;177m" # #d2a8ff
    GRAY = "\033[38;5;245m"    # gray for args

    LOG_FILE = _BASE / "logs" / "agent.log"
    TOOL_LOG_FILE = _BASE / "logs" / "tool_calls.log"

    @staticmethod
    def _timestamp():
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _write(level: str, message: str):
        try:
            Logger.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with Logger.LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(f"[{Logger._timestamp()}] [{level}] {message}\n")
        except Exception as e:
            # Fallback to stderr if logging fails
            print(f"[LOG ERROR] Could not write to log file: {e}")

    @staticmethod
    def _write_tool_log(entry: dict):
        """Write structured tool call entry to dedicated tool log file."""
        try:
            Logger.TOOL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with Logger.TOOL_LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[LOG ERROR] Could not write to tool log file: {e}")

    @staticmethod
    def info(message):
        print(f"{Logger.BLUE}[*]{Logger.RESET} [{Logger._timestamp()}] {message}")
        Logger._write("INFO", message)

    @staticmethod
    def success(message):
        print(f"{Logger.GREEN}[+]{Logger.RESET} [{Logger._timestamp()}] {message}")
        Logger._write("SUCCESS", message)

    @staticmethod
    def warn(message):
        print(f"{Logger.YELLOW}[!]{Logger.RESET} [{Logger._timestamp()}] {message}")
        Logger._write("WARNING", message)

    @staticmethod
    def error(message):
        print(f"{Logger.RED}[x]{Logger.RESET} [{Logger._timestamp()}] {message}")
        Logger._write("ERROR", message)

    @staticmethod
    def debug(message):
        print(f"{Logger.GRAY}[D]{Logger.RESET} [{Logger._timestamp()}] {message}")
        Logger._write("DEBUG", message)

    @staticmethod
    def tool(tool_name: str, args: dict):
        """Log a tool invocation with full arguments to console and structured log file."""
        display_names = {
            "read_file": "ReadFile",
            "list_directory": "ListDir",

            "replace": "EditFile",
            "write_file": "WriteFile",
            "run_shell_command": "RunShellCmd",
            "current_path": "CurrentPath",
            "search_file": "SearchFile",
            "glob": "Glob",
            "grep_search": "GrepSearch",
            "ask_user": "AskUser",
            "user_response": "UserResponse",
            "list_background_processes": "ListBgProcs",
            "read_background_output": "ReadBgOutput",
            "kill_process": "KillProcess",
            "enter_plan_mode": "EnterPlanMode",
            "google_web_search": "WebSearch",
            "web_fetch": "WebFetch",
            "code_interpreter": "CodeInterpreter",
        }

        display = display_names.get(tool_name, tool_name)

        # Build concise display message based on tool type
        if tool_name=='run_shell_command':
            msg = args.get("command", "<no command>")
            extra_parts = []
            if args.get("cwd"):
                extra_parts.append(f"cwd={args['cwd']}")
            if args.get("timeout"):
                extra_parts.append(f"timeout={args['timeout']}")
            if args.get("background"):
                extra_parts.append("BG=true")
            if extra_parts:
                msg += f" ({', '.join(extra_parts)})"
        elif tool_name == "grep_search":
            pattern = args.get("pattern", "<no pattern>")
            path = args.get("path", ".")
            include = args.get("include", "*")
            msg = f"pattern='{pattern}' in {path} (include={include})"
        elif tool_name == "glob":
            msg = f"pattern='{args.get('pattern', '<no pattern>')}' in {args.get('path', '.')}"
        elif tool_name == "replace":
            path = args.get("path", "<no path>")
            search_preview = (args.get("search", "")[:60] + "...") if len(args.get("search", "")) > 60 else args.get("search", "")
            replace_preview = (args.get("replace", "")[:60] + "...") if len(args.get("replace", "")) > 60 else args.get("replace", "")
            msg = f"{path} | '{search_preview}' -> '{replace_preview}'"
        elif tool_name == "write_file":
            path = args.get("path", "<no path>")
            content_len = len(args.get("content", ""))
            msg = f"{path} ({content_len} chars)"
        elif tool_name in ("list_directory", "read_file", "current_path"):
            msg = args.get("path", ".")
        elif tool_name == "read_background_output":
            msg = f"id={args.get('id', '<unknown>')}"
        elif tool_name == "kill_process":
            msg = f"id={args.get('id', '<unknown>')}"
        elif tool_name == "google_web_search":
            msg = f"query='{args.get('query', '<no query>')}'"
        elif tool_name == "web_fetch":
            msg = args.get("url", "<no url>")
        elif tool_name == "ask_user":
            question = args.get("question", "")
            msg = (question[:80] + "...") if len(question) > 80 else question
        elif tool_name == "user_response":
            desc = args.get("description", "")
            msg = (desc[:80] + "...") if len(desc) > 80 else desc
        elif tool_name == "list_background_processes":
            msg = "(list all)"
        elif tool_name == "enter_plan_mode":
            plan_value = args.get('plan', True)
            if isinstance(plan_value, str):
                plan_value = plan_value.lower() in ('true', '1', 'yes')
            msg = "(enter read-only mode)" if plan_value else "(exit read-only mode)"
        else:
            msg = json.dumps(args, ensure_ascii=False)

        # Console output
        check = f"{Logger.GREEN}\u2713{Logger.RESET}"
        args_str = json.dumps(args, ensure_ascii=False)
        print(f"{check} {Logger.BOLD}{display}{Logger.RESET} {Logger.CYAN}{msg}{Logger.RESET}")
        print(f"  {Logger.GRAY}Args: {args_str}{Logger.RESET} [{Logger._timestamp()}]")

        # Log the command specifically for run_shell_command
        if tool_name =="run_shell_command":
            command = args.get("command", "<no command>")
            Logger.info(f"Executing shell command: {command}")

        # General log
        Logger._write("TOOL", f"{display}: {msg} | Args: {args_str}")

        # Structured tool call log
        Logger._write_tool_log({
            "timestamp": Logger._timestamp(),
            "tool": tool_name,
            "display_name": display,
            "arguments": args,
            "summary": msg,
        })

    @staticmethod
    def tool_result(tool_name: str, result: dict):
        """Log the result of a tool execution."""
        status = result.get("status", "unknown")
        display_names = {
            "read_file": "ReadFile",
            "list_directory": "ListDir",

            "replace": "EditFile",
            "write_file": "WriteFile",
            "run_shell_command": "RunShellCmd",
            
            "current_path": "CurrentPath",
            "glob": "Glob",
            "grep_search": "GrepSearch",
            "list_background_processes": "ListBgProcs",
            "read_background_output": "ReadBgOutput",
            "kill_process": "KillProcess",
            "google_web_search": "WebSearch",
            "web_fetch": "WebFetch",
        }
        display = display_names.get(tool_name, tool_name)

        if status == "success":
            icon = f"{Logger.GREEN}\u2713{Logger.RESET}"
            level = "TOOL_RESULT_OK"
        else:
            icon = f"{Logger.RED}\u2717{Logger.RESET}"
            level = "TOOL_RESULT_ERR"

        result_summary = json.dumps(result.get("result", {}), ensure_ascii=False)
        if len(result_summary) > 200:
            result_summary = result_summary[:200] + "..."

        print(f"  {icon} {Logger.BOLD}{display} Result{Logger.RESET}: {result_summary} [{Logger._timestamp()}]")
        Logger._write(level, f"{display}: status={status} | {result_summary}")

        # Also append to structured tool log
        Logger._write_tool_log({
            "timestamp": Logger._timestamp(),
            "type": "result",
            "tool": tool_name,
            "display_name": display,
            "status": status,
            "result_summary": result_summary,
        })
