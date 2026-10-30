"""Natural-language questions answered by Claude with the store's query tools.

`ask()` runs a streaming tool-use loop and yields UI events:
    {"type": "conversation", "id"}        conversation id (send it back for follow-ups)
    {"type": "text", "text"}              answer text delta
    {"type": "tool_call", "id", "name", "input"}
    {"type": "tool_result", "id", "summary", "is_error"}
    {"type": "notice", "text"}            e.g. a refusal fallback switched models
    {"type": "done", "usage"}
    {"type": "error", "text"}

Conversations live in process memory (they hold opaque thinking blocks that must be
sent back unchanged), so a restart forgets them.

Credentials (APW_ASK_MODE):
    user    (default) each visitor supplies their own Anthropic API key with each request;
            the server's own credentials are never used. Safe for public deployment.
    server  only the server's credentials (ANTHROPIC_API_KEY / `ant auth login`).
    both    a visitor's key if given, else the server's.
A visitor's key is used for that request only: it is never stored, logged, or put in a
conversation; conversations remember only a keyed hash, so another key can't continue them.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import os
import re
import secrets
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, get_type_hints

import anthropic
import pydantic

from . import tools

MODEL = os.environ.get("APW_MODEL", "claude-opus-5-5")
MODELS = [m.strip() for m in os.environ.get("APW_ASK_MODELS", f"{MODEL},claude-sonnet-5-5").split(",")
          if m.strip()]
EFFORT = os.environ.get("APW_EFFORT", "medium")
MODE = os.environ.get("APW_ASK_MODE", "user")         # user | server | both
KEY_HEADER = "X-Anthropic-Api-Key"
_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,200}$")
_SECRET = secrets.token_bytes(32)                     # per-process salt for key fingerprints
MAX_ROUNDS = 12            # model calls per question
MAX_QUESTION_CHARS = 4000
MAX_CONVERSATIONS = 200
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """\
You answer questions about the phylogeny, classification, characters and literature of \
angiosperms (and other land plants) for botanists, using a knowledge store queried through \
your tools.

The store holds several sources, each with its own taxon concepts:
- 'apweb': P. F. Stevens' Angiosperm Phylogeny Website (latest snapshot) - the detailed \
source: clade hierarchy including informal bracketed clades such as \
"[Cornaceae + [Nyssaceae + ...]]", characterizations, genus/species counts, distributions, \
synonymy, and the literature APWeb cites by discussion topic.
- 'apg1' (1998), 'apg2' (2003), 'apg3' (2009), 'apg4' (2016): the APG classifications \
(orders, families, and their cladograms).
Names may mean different things in different sources; get_taxon's other_sources shows how \
each source places the same name. In characterizations, 'apomorphy': true marks states \
APWeb gives as possible apomorphies, and 'uncertain' marks states whose status in the clade \
is uncertain. Placements with 'alternatives' mean APWeb's own pages disagree.

How to answer:
- Base factual statements on tool results, and say which source each comes from \
("APWeb places...", "in APG IV..."). If the store doesn't cover something, say so; if you \
add general knowledge, label it clearly as not from the store.
- Link every taxon you mention that came from a tool result as [Label](/taxon/ID), and \
every reference as [Author et al. year](/reference/ID), using the ids from the results. \
Never make up an id; mention a taxon without a link if you have no id for it.
- When you already know the name, use get_taxon directly rather than searching first. \
Make independent tool calls in parallel.
- Write for a specialist: concise prose or short lists, a markdown table when comparing \
sources. Don't reproduce long passages of source text."""


# ------------------------------------------------------------------ tool schemas

def _args_model(fn) -> type[pydantic.BaseModel]:
    hints = get_type_hints(fn)
    fields = {}
    for p in inspect.signature(fn).parameters.values():
        default = ... if p.default is inspect.Parameter.empty else p.default
        fields[p.name] = (hints[p.name], default)
    return pydantic.create_model(f"{fn.__name__}_args", __config__=pydantic.ConfigDict(extra="forbid"),
                                 **fields)


def _strip_titles(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]
    return schema


ARG_MODELS = {fn.__name__: _args_model(fn) for fn in tools.ALL}
TOOL_DEFS = [{"name": fn.__name__,
              "description": inspect.cleandoc(fn.__doc__ or ""),
              "input_schema": _strip_titles(ARG_MODELS[fn.__name__].model_json_schema()),
              "eager_input_streaming": True}
             for fn in tools.ALL]


def run_tool(name: str, raw_input: Any) -> tuple[Any, bool]:
    """Validate the model's input against the tool's signature, then run it.
    With eager input streaming the API doesn't validate inputs, so this must."""
    fn = tools.BY_NAME.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}"}, True
    try:
        args = ARG_MODELS[name].model_validate(raw_input if isinstance(raw_input, dict) else {})
    except pydantic.ValidationError as e:
        return {"error": "invalid input", "details": e.errors(include_url=False),
                "received": raw_input}, True
    result = fn(**args.model_dump())
    return result, isinstance(result, dict) and "error" in result


def summarize(name: str, result: Any) -> str:
    """One line for the UI's activity list."""
    if isinstance(result, dict) and "error" in result:
        return str(result["error"])
    if isinstance(result, list):
        return f"{len(result)} result{'s' if len(result) != 1 else ''}"
    if name == "get_taxon":
        return f"{result['label']} ({result.get('rank')}, {result.get('source')})"
    if name == "get_literature":
        if "note" in result:
            return f"{len(result['topics'])} topics"
        n = sum(len(t["citations"]) for t in result["topics"])
        return f"{n} citations in {len(result['topics'])} topics"
    if name == "compare_classifications":
        return ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in result["counts"].items())
    if name == "clade_agreement":
        return f"{len(result['matched'])} matched, {len(result['unmatched'])} unmatched"
    if name == "get_reference":
        return (result.get("title") or result.get("raw", ""))[:80]
    if name == "get_subtree":
        return f"{result.get('label')}: {result.get('n_children', 0)} subgroups"
    return "done"


# ------------------------------------------------------------------ conversations

@dataclass
class Conversation:
    id: str
    owner: str                                  # fingerprint of the credentials that started it
    model: str
    messages: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_conversations: "OrderedDict[str, Conversation]" = OrderedDict()


def _conversation(cid: str | None, owner: str, model: str) -> Conversation:
    """Continue a conversation only for the credentials that started it."""
    conv = _conversations.get(cid) if cid else None
    if conv is not None and hmac.compare_digest(conv.owner, owner):
        _conversations.move_to_end(cid)
        return conv
    conv = Conversation(uuid.uuid4().hex, owner, model)
    _conversations[conv.id] = conv
    while len(_conversations) > MAX_CONVERSATIONS:
        _conversations.popitem(last=False)
    return conv


def echo_content(content: list) -> list[dict]:
    """Assistant content to send back next turn. Blocks are passed back unchanged, except
    that after a mid-output fallback the declined attempt's thinking and tool_use blocks
    (those before the last fallback marker) are dropped, as the API requires."""
    blocks = [b.to_dict() for b in content]
    last_fb = max((i for i, b in enumerate(blocks) if b["type"] == "fallback"), default=-1)
    out = []
    for i, b in enumerate(blocks):
        if b["type"] == "fallback":
            continue
        if i < last_fb and b["type"] in ("thinking", "redacted_thinking", "tool_use"):
            continue
        out.append(b)
    return out


_server_client: anthropic.AsyncAnthropic | None = None


def server_client() -> anthropic.AsyncAnthropic:
    global _server_client
    if _server_client is None:
        _server_client = anthropic.AsyncAnthropic()
    return _server_client


def user_client(api_key: str) -> anthropic.AsyncAnthropic:
    # An explicit api_key stops the SDK from consulting env vars or profiles, so the
    # server's own credentials can't leak into a visitor's requests.
    return anthropic.AsyncAnthropic(api_key=api_key)


def fingerprint(api_key: str | None) -> str:
    if api_key is None:
        return "server"
    return hmac.new(_SECRET, api_key.encode(), hashlib.sha256).hexdigest()


def valid_key(api_key: str) -> bool:
    return bool(_KEY_RE.match(api_key))


def server_configured() -> bool:
    """Whether server credentials look configured: an API key/token, or an `ant` profile."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE")
                or (Path.home() / ".config" / "anthropic").is_dir())


def status() -> dict:
    server = MODE in ("server", "both") and server_configured()
    return {"enabled": os.environ.get("APW_ASK_DISABLED") != "1" and (server or MODE != "server"),
            "mode": MODE, "server_key": server, "accepts_user_key": MODE in ("user", "both"),
            "models": MODELS, "model": MODELS[0]}


# ------------------------------------------------------------------ the loop

async def ask(question: str, conversation_id: str | None = None, api_key: str | None = None,
              model: str | None = None) -> AsyncIterator[dict]:
    """Answer a question; with `api_key` the request runs on the visitor's key."""
    api_key = (api_key or "").strip() or None
    if api_key is not None:
        if MODE == "server":
            yield {"type": "error", "auth": True,
                   "text": "This server answers with its own key; personal API keys aren't used."}
            return
        if not valid_key(api_key):
            yield {"type": "error", "auth": True,
                   "text": "That doesn't look like an Anthropic API key (they start with sk-ant-)."}
            return
    elif MODE == "user" or not server_configured():
        yield {"type": "error", "auth": True, "text": "Enter your Anthropic API key to ask questions."}
        return
    if model is not None and model not in MODELS:
        yield {"type": "error", "text": f"Unknown model {model!r}."}
        return

    question = question.strip()[:MAX_QUESTION_CHARS]
    conv = _conversation(conversation_id, fingerprint(api_key), model or MODELS[0])
    yield {"type": "conversation", "id": conv.id, "model": conv.model}
    if not question:
        yield {"type": "error", "text": "Empty question."}
        return
    if conv.lock.locked():
        yield {"type": "error", "text": "This conversation is still answering a question."}
        return
    client = user_client(api_key) if api_key else server_client()
    try:
        async for event in _answer(conv, question, client):
            if event["type"] == "error" and api_key:
                event["text"] = event["text"].replace(api_key, "[key]")
            yield event
    finally:
        if api_key:
            await client.close()


async def _answer(conv: Conversation, question: str,
                  client: anthropic.AsyncAnthropic) -> AsyncIterator[dict]:

    async with conv.lock:
        start = len(conv.messages)            # roll back to here if the turn fails
        conv.messages.append({"role": "user", "content": question})
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        json_retries = 0
        try:
            for _ in range(MAX_ROUNDS):
                try:
                    async with client.beta.messages.stream(
                        model=conv.model,
                        max_tokens=64000,
                        system=SYSTEM,
                        tools=TOOL_DEFS,
                        messages=conv.messages,
                        thinking={"type": "adaptive"},
                        output_config={"effort": EFFORT},
                        cache_control={"type": "ephemeral"},
                        betas=[FALLBACK_BETA],
                        fallbacks="default",
                    ) as stream:
                        async for event in stream:
                            if event.type == "content_block_start" and event.content_block.type == "fallback":
                                yield {"type": "notice",
                                       "text": f"Continuing on {event.content_block.to.model} "
                                               "after the first model declined."}
                            elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                                yield {"type": "text", "text": event.delta.text}
                        msg = await stream.get_final_message()
                    json_retries = 0
                except ValueError:
                    # tool input JSON the SDK couldn't parse at all; re-issue the turn
                    json_retries += 1
                    if json_retries > 2:
                        raise
                    continue

                for k in usage:
                    usage[k] += getattr(msg.usage, k, 0) or 0

                if msg.stop_reason == "refusal":
                    del conv.messages[start:]
                    yield {"type": "error", "text": "The model declined to answer this question."}
                    return
                content = echo_content(msg.content)
                conv.messages.append({"role": "assistant", "content": content})
                if msg.stop_reason == "pause_turn":
                    continue
                tool_uses = [b for b in content if b["type"] == "tool_use"]
                if not tool_uses:
                    yield {"type": "done", "usage": usage, "model": msg.model}
                    return
                if msg.stop_reason == "max_tokens":
                    raise RuntimeError("the answer was cut off (max_tokens)")

                results = []
                for b in tool_uses:
                    yield {"type": "tool_call", "id": b["id"], "name": b["name"], "input": b["input"]}
                outcomes = await asyncio.gather(
                    *(asyncio.to_thread(run_tool, b["name"], b["input"]) for b in tool_uses))
                for b, (result, is_error) in zip(tool_uses, outcomes):
                    yield {"type": "tool_result", "id": b["id"], "summary": summarize(b["name"], result),
                           "is_error": is_error}
                    results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": is_error,
                                    "content": json.dumps(result, ensure_ascii=False,
                                                          separators=(",", ":"), default=str)})
                conv.messages.append({"role": "user", "content": results})
            del conv.messages[start:]
            yield {"type": "error", "text": f"Stopped after {MAX_ROUNDS} steps without an answer."}
        except anthropic.AuthenticationError:
            del conv.messages[start:]
            yield {"type": "error", "auth": True, "text": "The Anthropic API rejected the API key."}
        except anthropic.PermissionDeniedError as e:
            del conv.messages[start:]
            yield {"type": "error", "auth": True, "text": f"The API key isn't permitted to do this: {e.message}"}
        except anthropic.RateLimitError:
            del conv.messages[start:]
            yield {"type": "error", "text": "Rate limited by the model API; try again shortly."}
        except anthropic.APIStatusError as e:
            del conv.messages[start:]
            yield {"type": "error", "text": f"Model API error {e.status_code}: {e.message}"}
        except anthropic.APIConnectionError:
            del conv.messages[start:]
            yield {"type": "error", "text": "Couldn't reach the model API."}
        except anthropic.AnthropicError as e:      # e.g. no credentials found
            del conv.messages[start:]
            yield {"type": "error", "text": f"The question service isn't available: {e}"}
        except Exception as e:
            del conv.messages[start:]
            yield {"type": "error", "text": f"Something went wrong: {e}"}
