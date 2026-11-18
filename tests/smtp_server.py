import base64
import socketserver
import threading
from dataclasses import dataclass, field


@dataclass
class Behaviour:
    reject_auth: bool = False
    reject_recipient: bool = False
    drop_during_data: bool = False
    messages: list[bytes] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    logins: list[str] = field(default_factory=list)


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        behaviour: Behaviour = self.server.behaviour
        self.reply("220 test ESMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            command = text.upper()
            if command.startswith("EHLO"):
                self.wfile.write(b"250-test\r\n250 AUTH PLAIN\r\n")
                self.wfile.flush()
            elif command.startswith("AUTH PLAIN"):
                if behaviour.reject_auth:
                    self.reply("535 authentication failed")
                else:
                    behaviour.logins.append(base64.b64decode(text.split()[-1]).decode(errors="replace"))
                    self.reply("235 ok")
            elif command.startswith("MAIL FROM"):
                self.reply("250 ok")
            elif command.startswith("RCPT TO"):
                if behaviour.reject_recipient:
                    self.reply("550 no such user")
                else:
                    behaviour.recipients.append(text.split(":", 1)[1].strip("<> "))
                    self.reply("250 ok")
            elif command == "DATA":
                self.reply("354 go ahead")
                body = []
                while True:
                    chunk = self.rfile.readline()
                    if chunk in (b".\r\n", b""):
                        break
                    body.append(chunk)
                    if behaviour.drop_during_data:
                        return
                behaviour.messages.append(b"".join(body))
                self.reply("250 queued")
            elif command == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("250 ok")


class SmtpServer:
    def __init__(self, behaviour: Behaviour | None = None) -> None:
        self.behaviour = behaviour or Behaviour()
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.behaviour = self.behaviour
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "SmtpServer":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
