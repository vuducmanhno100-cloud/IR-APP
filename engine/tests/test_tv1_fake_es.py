import socket

from ir_engine.testing.fake_es import FakeElasticsearch


def test_injected_errors_are_sent_only_after_the_request_body_is_read():
    """Replying before reading the body makes Windows reset the connection, so the client sees ELK_UNREACHABLE
    instead of the injected status. The fake must wait for the whole body even when it answers with an error."""
    body = b'{"index": []}'
    head = (
        "POST /_security/user/_has_privileges HTTP/1.1\r\nHost: fake\r\nAuthorization: ApiKey fake-api-key\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode()
    with FakeElasticsearch() as fake:
        fake.state.fail_next(429)
        host, port = fake.server.server_address
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(head)
            sock.settimeout(0.3)
            try:
                early = sock.recv(1024)
            except TimeoutError:
                early = b""
            assert early == b"", "fake answered before reading the body"
            sock.settimeout(5)
            sock.sendall(body)
            reply = sock.recv(4096)
    assert reply.startswith(b"HTTP/1.0 429")
