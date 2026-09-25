"""Runtime configuration loaded from environment / backend/.env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
LIVE_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
             "SMTP_STARTTLS", "EMAIL_FROM", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")
# Captured BEFORE load_dotenv: values injected by the host (Docker, a secrets manager) win and are fixed for the process.
_PROCESS_ENV = {k: os.environ[k].strip() for k in LIVE_KEYS if os.environ.get(k, "").strip()}
load_dotenv(BACKEND_DIR / ".env")

_env_file_cache: dict = {"key": None, "values": {}}


def _env_file_values(path: Path) -> dict[str, str]:
    """Current contents of the .env file, re-parsed only when the file changes (mtime + size)."""
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _env_file_cache["key"] != key:
        _env_file_cache.update(key=key, values={k: (v or "").strip() for k, v in dotenv_values(path).items()})
    return _env_file_cache["values"]


def live_env(name: str, env_file: Path) -> str | None:
    """A setting that follows edits to .env without restarting the API (used for the Google keys)."""
    return _PROCESS_ENV.get(name) or _env_file_values(env_file).get(name) or None


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    database_path: Path = field(
        default_factory=lambda: (BACKEND_DIR / os.getenv("DATABASE_PATH", "data/finance.db")).resolve()
    )
    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    )
    # Set COOKIE_SECURE=true when serving over HTTPS so the session cookie is never sent in clear text.
    cookie_secure: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() == "true")
    session_days: int = field(default_factory=lambda: int(os.getenv("SESSION_DAYS", "30")))
    demo_retention_days: int = field(default_factory=lambda: int(os.getenv("DEMO_RETENTION_DAYS", "7")))
    # Public URL of the web app: used for links in emails and the Google OAuth redirect URI.
    app_url: str = field(default_factory=lambda: os.getenv("APP_URL", "http://localhost:3000").rstrip("/"))
    outbox_dir: Path = field(default_factory=lambda: (BACKEND_DIR / os.getenv("OUTBOX_DIR", "data/outbox")).resolve())
    # Uploaded receipt files. Local disk is fine for one box; point this at a mounted volume (or
    # swap `receipts.store`/`path_for` for object storage) before running more than one API replica.
    receipts_dir: Path = field(default_factory=lambda: (BACKEND_DIR / os.getenv("RECEIPTS_DIR", "data/receipts")).resolve())
    # Google sign-in (OAuth 2.0 / OpenID Connect). Read live from env_file on every check, so adding or
    # removing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env switches the button on/off with no restart.
    env_file: Path = field(default_factory=lambda: BACKEND_DIR / ".env")
    # How many reverse proxies sit in front of the API and append to X-Forwarded-For. The browser
    # talks to Next.js, which proxies /api here, so 1 by default; add one per load balancer in front.
    # 0 = ignore the header and use the socket address. The client IP is the entry that many hops
    # from the right — the left-most value is whatever the client chose to send, so it's never trusted.
    trusted_proxy_hops: int = field(default_factory=lambda: int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
    log_format: str = field(default_factory=lambda: os.getenv("LOG_FORMAT", "text"))   # text | json

    @property
    def ip_rate_limits(self) -> bool:
        """Per-IP limits on sign-in, sign-up, demo and SMS forwarding. Read live so tests can switch them off."""
        return os.getenv("IP_RATE_LIMITS", "on").lower() != "off"
    google_client_id_override: str | None = None       # tests only
    google_client_secret_override: str | None = None   # tests only

    @property
    def google_client_id(self) -> str | None:
        return self.google_client_id_override or live_env("GOOGLE_CLIENT_ID", self.env_file)

    @property
    def google_client_secret(self) -> str | None:
        return self.google_client_secret_override or live_env("GOOGLE_CLIENT_SECRET", self.env_file)

    @property
    def google_enabled(self) -> bool:
        """Both the client id and the secret must be present; either missing disables Google sign-in."""
        return bool(self.google_client_id and self.google_client_secret)

    # Email delivery (SMTP), also read live from env_file. SMTP is used only when host, user AND password are
    # all set; otherwise emails go to the server log and outbox_dir (development mode).
    @property
    def smtp_host(self) -> str | None:
        return live_env("SMTP_HOST", self.env_file)

    @property
    def smtp_port(self) -> int:
        return int(live_env("SMTP_PORT", self.env_file) or 587)

    @property
    def smtp_user(self) -> str | None:
        return live_env("SMTP_USER", self.env_file)

    @property
    def smtp_password(self) -> str | None:
        pw = live_env("SMTP_PASSWORD", self.env_file)
        # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces aren't part of the password
        return pw.replace(" ", "") if pw and (self.smtp_host or "").lower() == "smtp.gmail.com" else pw

    @property
    def smtp_starttls(self) -> bool:
        return (live_env("SMTP_STARTTLS", self.env_file) or "true").lower() == "true"

    @property
    def email_from(self) -> str:
        return live_env("EMAIL_FROM", self.env_file) or (
            f"Ledgerly <{self.smtp_user}>" if self.smtp_user else "Ledgerly <no-reply@ledgerly.local>")

    # Razorpay (plan billing), read live like the keys above. Key id + secret enable checkout; the webhook
    # secret enables renewals / failed-payment / cancellation updates.
    @property
    def razorpay_key_id(self) -> str | None:
        return live_env("RAZORPAY_KEY_ID", self.env_file)

    @property
    def razorpay_key_secret(self) -> str | None:
        return live_env("RAZORPAY_KEY_SECRET", self.env_file)

    @property
    def razorpay_webhook_secret(self) -> str | None:
        return live_env("RAZORPAY_WEBHOOK_SECRET", self.env_file)

    @property
    def billing_enabled(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key) and not self.openai_api_key.startswith("sk-...")


settings = Settings()
