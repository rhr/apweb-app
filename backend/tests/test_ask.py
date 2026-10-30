"""The question-answering loop, against a scripted stand-in for the Anthropic client."""

import asyncio
import json
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

from apw import ask


class Block(NS):
    def to_dict(self):
        return {k: v for k, v in vars(self).items()}


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for b in self.msg.content:
                yield NS(type="content_block_start", content_block=b)
                if b.type == "text":
                    yield NS(type="content_block_delta", delta=NS(type="text_delta", text=b.text))
        return gen()

    async def get_final_message(self):
        return self.msg


class FakeClient:
    """Returns scripted messages in order and records each request."""

    def __init__(self, *messages):
        self.script = list(messages)
        self.requests = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kw):
        self.requests.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeStream(item)


def msg(*blocks, stop="end_turn"):
    return NS(content=list(blocks), stop_reason=stop, model="claude-opus-5-5",
              usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0))


KEY = "sk-ant-api03-" + "a" * 40
OTHER_KEY = "sk-ant-api03-" + "b" * 40


def run(question, cid=None, key=KEY, model=None):
    async def go():
        return [e async for e in ask.ask(question, cid, key, model)]
    return asyncio.run(go())


@pytest.fixture
def fake(monkeypatch):
    def install(*messages):
        c = FakeClient(*messages)
        c.used_keys = []

        def user_client(api_key):
            c.used_keys.append(api_key)
            c.close = lambda: asyncio.sleep(0)
            return c
        monkeypatch.setattr(ask, "user_client", user_client)
        monkeypatch.setattr(ask, "_server_client", c)
        monkeypatch.setattr(ask.tools, "get_taxon",
                            lambda name, source=None, max_characters=40: {"id": 7, "label": name,
                                                                          "rank": "family", "source": "apweb"})
        ask.tools.BY_NAME["get_taxon"] = ask.tools.get_taxon
        return c
    yield install
    ask.tools.BY_NAME["get_taxon"] = ask.tools.get_taxon


def test_tool_round_trip_and_request_shape(fake):
    c = fake(
        msg(Block(type="thinking", thinking="", signature="sig1"),
            Block(type="tool_use", id="t1", name="get_taxon", input={"name": "Vitaceae"}), stop="tool_use"),
        msg(Block(type="text", text="See [Vitaceae](/taxon/7).")),
    )
    events = run("Tell me about Vitaceae")
    kinds = [e["type"] for e in events]
    assert kinds == ["conversation", "tool_call", "tool_result", "text", "done"]
    assert events[2]["summary"] == "Vitaceae (family, apweb)"

    first, second = c.requests
    assert first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive"}
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    assert all(t["eager_input_streaming"] for t in first["tools"])
    # turn 2 replays the assistant turn unchanged (thinking signature kept) plus results
    assert second["messages"][1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig1"}
    assert second["messages"][2]["content"][0]["tool_use_id"] == "t1"

    # follow-up in the same conversation appends to the history
    c.script.append(msg(Block(type="text", text="Yes.")))
    run("And its order?", events[0]["id"])
    assert len(c.requests[2]["messages"]) == 5


def test_invalid_tool_input_is_returned_as_error(fake):
    c = fake(
        msg(Block(type="tool_use", id="t1", name="get_taxon", input={"nam": "x"}), stop="tool_use"),
        msg(Block(type="text", text="ok")),
    )
    events = run("q")
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and result["summary"] == "invalid input"
    assert c.requests[1]["messages"][2]["content"][0]["is_error"] is True


def test_fallback_echo_drops_declined_attempts_blocks(fake):
    c = fake(
        msg(Block(type="thinking", thinking="", signature="a"),
            Block(type="text", text="Partial "),
            Block(type="fallback", **{"from": NS(model="claude-opus-5-5")}, to=NS(model="claude-opus-5")),
            Block(type="tool_use", id="t2", name="get_taxon", input={"name": "Poaceae"}), stop="tool_use"),
        msg(Block(type="text", text="done")),
    )
    events = run("q")
    assert any(e["type"] == "notice" and "claude-opus-5" in e["text"] for e in events)
    echoed = c.requests[1]["messages"][1]["content"]
    assert [b["type"] for b in echoed] == ["text", "tool_use"]


def test_refusal_rolls_back_the_turn(fake):
    c = fake(msg(stop="refusal"))
    events = run("q")
    assert events[-1]["type"] == "error" and "declined" in events[-1]["text"]
    conv = ask._conversations[events[0]["id"]]
    assert conv.messages == []


def test_auth_error_is_reported(fake):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=req), body=None)
    fake(err)
    events = run("q")
    assert events[-1]["type"] == "error" and "API key" in events[-1]["text"]


def test_ask_endpoint_streams_sse(fake):
    from fastapi.testclient import TestClient
    from apw.api import app
    c = fake(msg(Block(type="text", text="Hello")))
    with TestClient(app).stream("POST", "/api/ask", json={"question": "hi"},
                                headers={"X-Anthropic-Api-Key": KEY}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [l for l in r.iter_lines() if l.startswith("data: ")]
    import json
    events = [json.loads(l[6:]) for l in lines]
    assert [e["type"] for e in events] == ["conversation", "text", "done"]
    assert c.used_keys == [KEY]


# ------------------------------------------------------------------ bring-your-own-key


def test_user_mode_requires_a_valid_key(fake, monkeypatch):
    monkeypatch.setattr(ask, "MODE", "user")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key-should-never-be-used")
    c = fake(msg(Block(type="text", text="x")))
    for key, words in ((None, "Enter your Anthropic API key"), ("hunter2", "doesn't look like")):
        events = run("q", key=key)
        assert events[-1]["type"] == "error" and events[-1]["auth"] and words in events[-1]["text"]
    assert c.requests == []                     # nothing reached the model


def test_server_mode_ignores_personal_keys(fake, monkeypatch):
    monkeypatch.setattr(ask, "MODE", "server")
    fake()
    events = run("q", key=KEY)
    assert events[-1]["type"] == "error" and "personal API keys" in events[-1]["text"]


def test_both_mode_falls_back_to_server_credentials(fake, monkeypatch):
    monkeypatch.setattr(ask, "MODE", "both")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server")
    c = fake(msg(Block(type="text", text="ok")))
    events = run("q", key=None)
    assert events[-1]["type"] == "done" and c.used_keys == []


def test_conversation_is_bound_to_its_key_and_model(fake):
    c = fake(msg(Block(type="text", text="one")), msg(Block(type="text", text="two")),
             msg(Block(type="text", text="three")))
    first = run("q1", model="claude-sonnet-5-5")
    assert first[0]["model"] == "claude-sonnet-5-5"
    cid = first[0]["id"]
    same = run("q2", cid, KEY)                  # same key: continues, keeps its model
    assert same[0]["id"] == cid and len(c.requests[1]["messages"]) == 3
    assert c.requests[1]["model"] == "claude-sonnet-5-5"
    other = run("q3", cid, OTHER_KEY)           # another key can't read or extend it
    assert other[0]["id"] != cid and len(c.requests[2]["messages"]) == 1
    conv = ask._conversations[cid]
    assert KEY not in json.dumps(conv.messages) and KEY not in conv.owner


def test_key_is_scrubbed_from_errors(fake):
    fake(RuntimeError(f"boom with {KEY} inside"))
    events = run("q")
    assert events[-1]["type"] == "error" and KEY not in events[-1]["text"] and "[key]" in events[-1]["text"]


def test_unknown_model_rejected(fake):
    fake()
    assert run("q", model="gpt-4")[-1]["text"].startswith("Unknown model")


def test_explicit_key_ignores_server_environment(monkeypatch):
    # the SDK must not mix the server's token into a visitor's client
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "server-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key")
    client = ask.user_client(KEY)
    assert client.api_key == KEY and client.auth_token is None
