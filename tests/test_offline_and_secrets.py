import os
import socket
import stat

import pytest

from jobpipe.contract.offline import NetworkBlocked, block_network, offline_requested
from jobpipe.contract.secrets import SecretsError, load_secret_registry, send_enabled


def test_session_guard_blocks_external_connections() -> None:
    assert offline_requested()
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    sock = socket.socket()
    try:
        with pytest.raises(NetworkBlocked):
            sock.connect(("203.0.113.9", 443))
        with pytest.raises(NetworkBlocked):
            sock.connect_ex(("example.org", 443))
    finally:
        sock.close()


def test_loopback_stays_reachable() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        client.close()
        probe = socket.socket()
        assert probe.connect_ex(("localhost", port)) == 0
        probe.close()
    finally:
        server.close()


def test_guard_is_restored_on_exit() -> None:
    before = socket.socket.connect
    with block_network():
        assert socket.socket.connect is not before
    assert socket.socket.connect is before


def test_send_is_disabled_during_tests() -> None:
    assert os.environ["JOBPIPE_SEND"] == "0"
    assert send_enabled() is False


def write_secrets(path, mode: int = 0o600) -> None:
    path.write_text(
        'ollama_host = "http://localhost:11434"\n[smtp]\nhost = "smtp.example.org"\nusername = "u"\n'
        'password = "hunter2-secret"\nsender = "me@example.org"\n',
        encoding="utf-8",
    )
    path.chmod(mode)


def test_secrets_load_from_a_restricted_file_outside_the_repository(tmp_path) -> None:
    target = tmp_path / "secrets.toml"
    write_secrets(target)
    registry = load_secret_registry(target)
    assert registry.require_smtp().host == "smtp.example.org"
    assert "hunter2" not in repr(registry)
    assert "hunter2" not in repr(registry.smtp)
    with pytest.raises(SecretsError):
        registry.require_telegram()


def test_secrets_file_must_not_be_group_or_world_readable(tmp_path) -> None:
    target = tmp_path / "secrets.toml"
    write_secrets(target, 0o644)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    with pytest.raises(SecretsError, match="chmod 600"):
        load_secret_registry(target)


def test_secrets_file_inside_a_git_repository_is_refused(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    target = tmp_path / "secrets.toml"
    write_secrets(target)
    with pytest.raises(SecretsError, match="outside any git repository"):
        load_secret_registry(target)


def test_missing_secrets_file_yields_an_empty_registry(tmp_path) -> None:
    registry = load_secret_registry(tmp_path / "absent.toml")
    assert registry.smtp is None and registry.telegram is None


def test_unknown_secret_keys_fail_loudly(tmp_path) -> None:
    target = tmp_path / "secrets.toml"
    target.write_text('smtp_pasword = "x"\n', encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(Exception):
        load_secret_registry(target)
