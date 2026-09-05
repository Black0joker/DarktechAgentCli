import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

# Token file JSON path
# When running as a PyInstaller bundle, use the exe's directory for writable config.
# Otherwise (development), use the project root config folder.
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    _BASE_DIR = Path(sys.executable).parent
else:
    # Running in development
    _BASE_DIR = Path(__file__).parent.parent.parent

TOKEN_FILE = _BASE_DIR / "config" / "tokens.json"
CONFIG_FILE = _BASE_DIR / "config" / "config.json"

# Ensure the config directory and tokens.json file exist
if not TOKEN_FILE.exists():
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
        json.dump({}, f, indent=2)

# Ensure config.json exists with defaults
if not CONFIG_FILE.exists():
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            "DEFAULT_PROXY_URL": "https://darktechapi.runasp.net",
            "CAPTCHA_SOLVER_API_BASE": "https://capsolver.runasp.net"
        }, f, indent=2)


def load_config() -> dict:
    """Load configuration from config/config.json. Returns empty dict on failure."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}



def load_tokenfile() -> dict:
    """Load token file from JSON file. Returns empty dict if file doesn't exist."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_tokenfile(tokenfile_dict: dict) -> bool:
    """Save token file to JSON file. Returns True on success."""
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(tokenfile_dict, f, indent=2)
        return True
    except IOError:
        return False


def get_active_token() -> Optional[str]:
    """Get the first active token from the token file.

    Supports both the new 'tokens' list structure and the legacy 'token' field.
    Returns None if no token is available.
    """
    token_data = load_tokenfile()
    tokens = token_data.get('tokens')
    if isinstance(tokens, list) and tokens:
        return tokens[0]
    # Fallback to legacy 'token' field
    legacy = token_data.get('token')
    if legacy:
        return legacy
    return None


def remove_first_token() -> bool:
    """Remove the first (active) token from the tokens list and save the token file.

    Returns True if a token was removed and saved, False otherwise.
    Also strips any legacy 'token' field to enforce the new structure.
    """
    token_data = load_tokenfile()
    tokens = token_data.get('tokens', [])
    if isinstance(tokens, list) and tokens:
        tokens.pop(0)
        token_data['tokens'] = tokens
        # Drop legacy 'token' field if present
        token_data.pop('token', None)
        return save_tokenfile(token_data)
    # Fallback: if legacy 'token' exists, remove it
    if 'token' in token_data:
        token_data.pop('token', None)
        return save_tokenfile(token_data)
    return False


def rotate_first_token() -> bool:
    """Move the first (active) token to the end of the tokens list and save.

    Used by rate-limit rotation: the exhausted token is preserved (its daily
    quota resets later) but the next token in line becomes the active one.
    Returns True if a rotation happened and was saved, False otherwise.
    """
    token_data = load_tokenfile()
    tokens = token_data.get('tokens', [])
    if isinstance(tokens, list) and len(tokens) > 1:
        tokens.append(tokens.pop(0))
        token_data['tokens'] = tokens
        # Drop legacy 'token' field if present
        token_data.pop('token', None)
        return save_tokenfile(token_data)
    return False


def get_token_count() -> int:
    """Get the number of available tokens."""
    token_data = load_tokenfile()
    tokens = token_data.get('tokens', [])
    if isinstance(tokens, list):
        return len(tokens)
    if token_data.get('token'):
        return 1
    return 0


def add_token(token: str) -> bool:
    """Append a token to the tokens list and save the token file.

    Returns True if the token was added and saved successfully.
    """
    token_data = load_tokenfile()
    tokens = token_data.get('tokens', [])
    if not isinstance(tokens, list):
        tokens = []
    tokens.append(token)
    token_data['tokens'] = tokens
    return save_tokenfile(token_data)


# ---------------------------------------------------------------------------
# Darktech Proxy URL configuration (see docs/API.md)
# ---------------------------------------------------------------------------

_app_config = load_config()
DEFAULT_PROXY_URL = _app_config.get("DEFAULT_PROXY_URL", "https://darktechapi.runasp.net")
CAPTCHA_SOLVER_API_BASE = _app_config.get("CAPTCHA_SOLVER_API_BASE", "https://capsolver.runasp.net")


def get_proxy_url() -> str:
    """Return the Darktech Proxy base URL (without trailing slash).

    key in tokens.json, then the documented default http://127.0.0.1:8001.
    """
    url = load_tokenfile().get('DEFAULT_Darktech_URL')
    if isinstance(url, str) and url.strip():
        return url.strip().rstrip('/')
    return DEFAULT_PROXY_URL


def set_proxy_url(url: str) -> bool:
    """Persist the Darktech Proxy base URL. Returns True on success."""
    token_data = load_tokenfile()
    token_data['DEFAULT_Darktech_URL'] = url.strip().rstrip('/')
    return save_tokenfile(token_data)

