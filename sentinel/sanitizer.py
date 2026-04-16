"""
Sanitizer Module — Masks sensitive data before sending alerts to Discord.

Detects and redacts:
- Passwords, tokens, API keys, secrets in key=value or key: value formats
- Connection strings (postgresql://, mysql://, mongodb://, redis://, amqp://)
- Bearer/Basic authorization tokens
- AWS access keys, private keys
- Generic high-entropy strings that look like secrets

Usage:
    from sentinel.sanitizer import sanitize
    clean_text = sanitize("DB_PASSWORD=hunter2")
    # → "DB_PASSWORD=[REDACTED]"
"""

import re

# ─── Patterns ──────────────────────────────────────────────────────────────────

# Keywords whose values should always be redacted (case-insensitive).
# Matches: KEY=value, KEY: value, KEY = "value", KEY="value", etc.
_SENSITIVE_KEYS = (
    r"password|passwd|pwd|secret|token|api_?key|apikey|"
    r"access_?key|private_?key|credentials?|auth|"
    r"db_pass|database_url|connection_string|dsn|"
    r"webhook_?url|webhook_?secret|"
    r"smtp_pass|mail_pass|"
    r"jwt|session_?secret|signing_?key|encryption_?key|"
    r"client_?secret|app_?secret|master_?key"
)

# KEY=VALUE or KEY: VALUE pattern (handles quotes and whitespace)
_KV_PATTERN = re.compile(
    rf'(?i)({_SENSITIVE_KEYS})'           # group 1: the key name
    rf'(\s*[=:]\s*)'                       # group 2: separator (= or :)
    rf'(["\']?)(\S+?)\3'                   # group 3: optional quote, group 4: value
    rf'(?=\s|$|[,;\]\}})])',               # lookahead: ends at whitespace/delimiter/end
    re.IGNORECASE,
)

# Connection strings: scheme://user:pass@host/db
_CONN_STRING = re.compile(
    r'(?i)((?:postgresql|postgres|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)'
    r'://)'                                # group 1: scheme
    r'([^@\s]+)'                           # group 2: credentials part
    r'(@[^\s"\']+)',                        # group 3: @host/db
)

# Bearer / Basic tokens in text
_AUTH_HEADER = re.compile(
    r'(?i)((?:Bearer|Basic|Token)\s+)(\S+)',
)

# AWS access key IDs (AKIA...)
_AWS_KEY = re.compile(
    r'(AKIA[0-9A-Z]{16})',
)

# Generic long hex/base64 that looks like a secret (32+ chars of hex/alnum)
_LONG_HEX = re.compile(
    r'(?<![a-zA-Z0-9/])([a-fA-F0-9]{40,})(?![a-zA-Z0-9/])',
)

# Private key blocks
_PRIVATE_KEY = re.compile(
    r'(-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----)'
    r'(.*?)'
    r'(-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----)',
    re.DOTALL,
)


# ─── Public API ────────────────────────────────────────────────────────────────

def sanitize(text: str) -> str:
    """
    Sanitize a string by replacing sensitive values with [REDACTED].

    This function is idempotent and safe to call on already-clean text.
    It does NOT modify non-sensitive content.

    Args:
        text: The raw text that may contain secrets.

    Returns:
        Sanitized text with secrets replaced by [REDACTED].
    """
    if not text:
        return text

    # 1. Private key blocks
    text = _PRIVATE_KEY.sub(r'\1[REDACTED]\3', text)

    # 2. Connection strings (mask credentials, keep scheme and host)
    text = _CONN_STRING.sub(r'\1[REDACTED]\3', text)

    # 3. Auth headers (Bearer/Basic tokens)
    text = _AUTH_HEADER.sub(r'\1[REDACTED]', text)

    # 4. Key=value pairs with sensitive key names
    text = _KV_PATTERN.sub(r'\1\2[REDACTED]', text)

    # 5. AWS access keys
    text = _AWS_KEY.sub('[REDACTED]', text)

    # 6. Long hex strings (likely tokens/hashes — only 40+ chars)
    text = _LONG_HEX.sub('[REDACTED]', text)

    return text
