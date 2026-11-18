import ipaddress
import os
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

OFFLINE_FLAG = "JOBPIPE_OFFLINE"


class NetworkBlocked(RuntimeError):
    pass


def offline_requested() -> bool:
    return os.environ.get(OFFLINE_FLAG, "0") == "1"


def _is_loopback(host: object) -> bool:
    if not isinstance(host, str):
        return False
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _target_allowed(address: Any) -> bool:
    if isinstance(address, (str, bytes)):
        return True
    if isinstance(address, tuple) and address:
        return _is_loopback(address[0])
    return False


@contextmanager
def block_network() -> Iterator[None]:
    original_connect: Callable[..., Any] = socket.socket.connect
    original_connect_ex: Callable[..., Any] = socket.socket.connect_ex
    original_create = socket.create_connection

    def connect(self: socket.socket, address: Any) -> Any:
        if not _target_allowed(address):
            raise NetworkBlocked(f"network access is disabled in tests: connect to {address!r}")
        return original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if not _target_allowed(address):
            raise NetworkBlocked(f"network access is disabled in tests: connect_ex to {address!r}")
        return original_connect_ex(self, address)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if not _target_allowed(address):
            raise NetworkBlocked(f"network access is disabled in tests: create_connection to {address!r}")
        return original_create(address, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = original_create
