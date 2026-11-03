import os
import stat
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

SECRETS_FILE_ENV = "JOBPIPE_SECRETS_FILE"
SEND_FLAG = "JOBPIPE_SEND"
DEFAULT_SECRETS_PATH = Path("~/.config/jobpipe/secrets.toml")


class SecretsError(Exception):
    pass


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SmtpSecrets(_Section):
    host: str = Field(min_length=1)
    port: int = Field(default=587, gt=0)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1, repr=False)
    sender: str = Field(min_length=3)
    starttls: bool = True


class ImapSecrets(_Section):
    host: str = Field(min_length=1)
    port: int = Field(default=993, gt=0)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1, repr=False)


class TelegramSecrets(_Section):
    bot_token: str = Field(min_length=10, repr=False)
    chat_id: str = Field(min_length=1)


class SecretRegistry(_Section):
    database_path: Path | None = None
    ollama_host: str | None = None
    smtp: SmtpSecrets | None = None
    imap: ImapSecrets | None = None
    telegram: TelegramSecrets | None = None

    def __repr__(self) -> str:
        configured = [name for name in ("smtp", "imap", "telegram") if getattr(self, name) is not None]
        return f"SecretRegistry(configured={configured})"

    def require_smtp(self) -> SmtpSecrets:
        if self.smtp is None:
            raise SecretsError("SMTP credentials are not configured in the secrets file")
        return self.smtp

    def require_imap(self) -> ImapSecrets:
        if self.imap is None:
            raise SecretsError("IMAP credentials are not configured in the secrets file")
        return self.imap

    def require_telegram(self) -> TelegramSecrets:
        if self.telegram is None:
            raise SecretsError("Telegram credentials are not configured in the secrets file")
        return self.telegram


def send_enabled() -> bool:
    return os.environ.get(SEND_FLAG, "0") == "1"


def _inside_repository(path: Path) -> bool:
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return True
    return False


def resolve_secrets_path(explicit: Path | str | None = None) -> Path:
    raw = explicit or os.environ.get(SECRETS_FILE_ENV) or DEFAULT_SECRETS_PATH
    return Path(raw).expanduser().resolve()


def load_secret_registry(explicit: Path | str | None = None) -> SecretRegistry:
    path = resolve_secrets_path(explicit)
    if _inside_repository(path):
        raise SecretsError(f"the secrets file must live outside any git repository: {path}")
    if not path.is_file():
        return SecretRegistry()
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SecretsError(f"the secrets file must not be readable by group or others (chmod 600): {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SecretsError(f"the secrets file is not valid TOML: {exc}") from exc
    return SecretRegistry.model_validate(data)
