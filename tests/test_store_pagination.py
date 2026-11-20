"""Tests for transcript pagination in the SQLite store.

The UI opens a conversation with only the newest page of items and
prepends older pages while the user scrolls; the store must return
bounded windows oldest-first and never split a function_call from its
function_call_output across a page boundary.
"""

from __future__ import annotations

from pathlib import Path

from codelite.db.store import Store


def _make_store(tmp_path: Path) -> tuple[Store, str]:
    store = Store(tmp_path / "codelite.db")
    conversation = store.create_conversation(
        workspace=str(tmp_path), model="gpt-5.2", permission_mode="default"
    )
    return store, conversation.id


def _append(store: Store, conversation_id: str, items: list[dict]) -> None:
    store.append_items(conversation_id, items)


def test_entries_carry_item_ids(tmp_path: Path) -> None:
    store, cid = _make_store(tmp_path)
    _append(store, cid, [{"role": "user", "content": "hi"}])
    entry = store.load_entries(cid)[0]
    assert isinstance(entry["id"], int)


def test_window_returns_newest_page_oldest_first(tmp_path: Path) -> None:
    store, cid = _make_store(tmp_path)
    _append(store, cid, [
        {"role": "user", "content": f"message {i}"} for i in range(10)
    ])
    page = store.load_entries(cid, limit=3, before_id=None)
    assert len(page) == 3
    # The three newest items, in original order.
    texts = [entry["payload"]["content"] for entry in page]
    assert texts == ["message 7", "message 8", "message 9"]


def test_before_cursor_pages_backwards(tmp_path: Path) -> None:
    store, cid = _make_store(tmp_path)
    _append(store, cid, [
        {"role": "user", "content": f"message {i}"} for i in range(10)
    ])
    first = store.load_entries(cid, limit=4)
    cursor = first[0]["id"]
    older = store.load_entries(cid, limit=4, before_id=cursor)
    texts = [entry["payload"]["content"] for entry in older]
    assert texts == ["message 2", "message 3", "message 4", "message 5"]
    assert store.has_items_before(cid, older[0]["id"])
    oldest = store.load_entries(cid, limit=4, before_id=older[0]["id"])
    assert [entry["payload"]["content"] for entry in oldest] == [
        "message 0", "message 1",
    ]
    assert not store.has_items_before(cid, oldest[0]["id"])


def test_page_is_extended_to_close_open_tool_calls(tmp_path: Path) -> None:
    store, cid = _make_store(tmp_path)
    _append(store, cid, [
        {"role": "user", "content": "run it"},
        {"type": "function_call", "call_id": "call-1", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-1", "output": "ok"},
        {"type": "function_call", "call_id": "call-2", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-2", "output": "fine"},
        {"role": "assistant", "content": "done"},
    ])
    # A window whose newest edge falls between call-2 and its output must
    # pull the output in, so no page ends on an open tool call.
    call2 = store.load_entries(cid)[3]
    page = store.load_entries(cid, limit=3, before_id=call2["id"] + 1)
    types = [entry["payload"]["type"] for entry in page]
    assert types == [
        "function_call", "function_call_output",
        "function_call", "function_call_output",
    ]


def test_unpaged_load_stays_complete(tmp_path: Path) -> None:
    store, cid = _make_store(tmp_path)
    _append(store, cid, [
        {"role": "user", "content": "hi"},
        {"type": "function_call", "call_id": "call-1", "name": "t", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-1", "output": "ok"},
    ])
    entries = store.load_entries(cid)
    assert len(entries) == 3
    assert [entry["payload"] for entry in entries] == store.load_items(cid)
