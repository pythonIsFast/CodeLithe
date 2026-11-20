from codelite.provider.config import ProviderConfig
from codelite.provider.sse import iterate_server_sent_events
from codelite.provider.transport import CodexTransport


def test_codex_stream_returns_partial_socket_reads_and_keeps_split_utf8():
    class Response:
        closed = False

        def __init__(self):
            self.chunks = iter((b'data: "f\xc3', b'\xbcr"\n\n', b""))

        def read1(self, _size):
            return next(self.chunks)

        def read(self, _size):
            raise AssertionError("buffering read would delay the first SSE chunk")

        def close(self):
            self.closed = True

    response = Response()
    transport = CodexTransport(ProviderConfig(), lambda: None)
    chunks = transport._iter_body(response)
    assert next(chunks) == b'data: "f\xc3'
    # Start a fresh stream to test decoding across the same chunk boundary.
    chunks.close()
    assert response.closed
    stream = Response()
    events = list(iterate_server_sent_events(transport._iter_body(stream)))
    assert [event.data for event in events] == ['"für"']
    assert stream.closed
