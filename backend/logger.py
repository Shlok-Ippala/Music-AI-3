"""
Terminal logger for Music AI.
Prints colored I/O and tool calls to the terminal while the app runs.
"""

import json
import inspect
import time
from datetime import datetime
from functools import wraps

# ANSI colors
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"
MAGENTA = "\033[35m"
WHITE  = "\033[97m"


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _truncate(val, max_len=120):
    s = json.dumps(val) if not isinstance(val, str) else val
    return s[:max_len] + "..." if len(s) > max_len else s


def log_user(text: str):
    print(f"\n{BOLD}{BLUE}[{_ts()}] YOU{RESET}  {WHITE}{text}{RESET}")


def log_ai(text: str):
    print(f"\n{BOLD}{GREEN}[{_ts()}] AI{RESET}   {WHITE}{text}{RESET}\n")


def log_status(text: str):
    print(f"{DIM}[{_ts()}] {text}{RESET}")


def log_error(text: str):
    print(f"{BOLD}{RED}[{_ts()}] ERROR  {text}{RESET}")


def _log_tool_call(name: str, args: dict):
    args_str = ", ".join(f"{k}={_truncate(v, 60)}" for k, v in args.items())
    print(f"  {CYAN}→ {BOLD}{name}{RESET}{CYAN}({args_str}){RESET}")


def _log_tool_result(name: str, result: dict, elapsed: float):
    ok = result.get("ok", True)
    status = f"{GREEN}ok{RESET}" if ok else f"{RED}error{RESET}"
    detail = ""
    if not ok:
        detail = f" — {result.get('error', '')}"
    elif "total_notes" in result:
        detail = f" — {result['total_notes']} notes"
    elif "tracks" in result:
        detail = f" — {len(result['tracks'])} tracks"
    elif "ret" in result:
        detail = f" — {_truncate(result['ret'], 40)}"
    print(f"  {DIM}← {name} [{status}{DIM}]{detail} ({elapsed:.2f}s){RESET}")


def log_broadcast(text: str):
    """Callback for Railtracks broadcast() calls — prints streamed agent thoughts."""
    print(f"  {MAGENTA}💭 {text}{RESET}")


def llm_decision_hook(message_history, response):
    """
    Post-hook attached to the LLM — fires after every model call.
    Logs tool call decisions and final text responses.
    """
    msg = response.message
    content = msg.content

    if isinstance(content, list):
        # Agent decided to call tools — show each one with its reasoning
        print(f"\n{BOLD}{YELLOW}[{_ts()}] AGENT THINKING{RESET}")
        for tc in content:
            args_preview = ", ".join(
                f"{k}={_truncate(v, 50)}" for k, v in (tc.arguments or {}).items()
            )
            print(f"  {YELLOW}⚙  calling {BOLD}{tc.name}{RESET}{YELLOW}({args_preview}){RESET}")
    elif isinstance(content, str) and content.strip():
        # Agent produced text (either reasoning mid-loop or final answer preview)
        preview = content.strip().replace("\n", " ")[:200]
        print(f"\n{BOLD}{MAGENTA}[{_ts()}] AGENT RESPONSE{RESET}  {DIM}{preview}{RESET}")

    return response


def patch_tools(tools: dict) -> dict:
    """
    Wrap every tool function to log its call and result to the terminal.
    Returns the same dict with wrapped functions.
    """
    patched = {}
    for name, func in tools.items():
        patched[name] = _make_wrapper(name, func)
    return patched


def _make_wrapper(name: str, func):
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            all_args = _build_args(func, args, kwargs)
            _log_tool_call(name, all_args)
            t0 = time.time()
            try:
                result = await func(*args, **kwargs)
                _log_tool_result(name, result if isinstance(result, dict) else {}, time.time() - t0)
                return result
            except Exception as e:
                print(f"  {RED}← {name} raised {e}{RESET}")
                raise
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            all_args = _build_args(func, args, kwargs)
            _log_tool_call(name, all_args)
            t0 = time.time()
            try:
                result = func(*args, **kwargs)
                _log_tool_result(name, result if isinstance(result, dict) else {}, time.time() - t0)
                return result
            except Exception as e:
                print(f"  {RED}← {name} raised {e}{RESET}")
                raise
        return sync_wrapper


def _build_args(func, args, kwargs):
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except Exception:
        return {f"arg{i}": v for i, v in enumerate(args)} | kwargs
