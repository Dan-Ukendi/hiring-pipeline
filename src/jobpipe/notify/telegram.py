from dataclasses import dataclass, field
from typing import Any, Protocol

from jobpipe.contract.secrets import TelegramSecrets
from jobpipe.http.client import HttpClient, HttpTransportError

API_ROOT = "https://api.telegram.org"
APPROVE = "approve"
DISMISS = "dismiss"


class TelegramApi(Protocol):
    def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class HttpTelegramApi:
    def __init__(self, secrets: TelegramSecrets, http: HttpClient) -> None:
        self._secrets = secrets
        self._http = http

    def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{API_ROOT}/bot{self._secrets.bot_token}/{method}"
        try:
            result = self._http.post_json(url, payload)
        except HttpTransportError as exc:
            raise RuntimeError(f"Telegram request failed: {type(exc).__name__}") from None
        if not result.ok:
            raise RuntimeError(f"Telegram answered HTTP {result.status_code}")
        return result.json()


@dataclass(frozen=True)
class PendingApproval:
    application_id: int
    title: str
    company: str | None
    reason: str


@dataclass
class Digest:
    sent: int = 0
    unconfirmed: int = 0
    bounced: int = 0
    needs_manual: int = 0
    awaiting_review: int = 0
    pending: list[PendingApproval] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)


def format_digest(digest: Digest, report_path: str | None) -> str:
    lines = [f"jobpipe: {digest.sent} sent, {digest.unconfirmed} unconfirmed, {digest.bounced} bounced, "
             f"{digest.needs_manual} for manual work, {digest.awaiting_review} documents to review"]
    lines.extend(f"! {alert}" for alert in digest.alerts)
    if report_path:
        lines.append(f"Report: {report_path}")
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, api: TelegramApi, secrets: TelegramSecrets) -> None:
        self._api = api
        self._chat = secrets.chat_id

    def send_digest(self, digest: Digest, report_path: str | None) -> None:
        self._api.call("sendMessage", {"chat_id": self._chat, "text": format_digest(digest, report_path)})
        for item in digest.pending:
            keyboard = {"inline_keyboard": [[
                {"text": "Approve and queue", "callback_data": f"{APPROVE}:{item.application_id}"},
                {"text": "Leave for later", "callback_data": f"{DISMISS}:{item.application_id}"},
            ]]}
            text = f"Approval needed ({item.reason}): {item.company or ''} — {item.title}"
            self._api.call("sendMessage", {"chat_id": self._chat, "text": text, "reply_markup": keyboard})

    def alert(self, text: str) -> None:
        self._api.call("sendMessage", {"chat_id": self._chat, "text": f"! {text}"})

    def poll_decisions(self, offset: int | None = None) -> tuple[list[tuple[str, int, str]], int | None]:
        payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        response = self._api.call("getUpdates", payload)
        decisions: list[tuple[str, int, str]] = []
        last = offset
        for update in response.get("result", []):
            last = int(update["update_id"]) + 1
            query = update.get("callback_query")
            if not query:
                continue
            sender = str((query.get("from") or {}).get("id", ""))
            chat = str(((query.get("message") or {}).get("chat") or {}).get("id", ""))
            if chat != self._chat and sender != self._chat:
                continue
            action, _, raw_id = str(query.get("data", "")).partition(":")
            if action in {APPROVE, DISMISS} and raw_id.isdigit():
                decisions.append((action, int(raw_id), str(query.get("id"))))
        return decisions, last

    def acknowledge(self, callback_id: str, text: str) -> None:
        self._api.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
