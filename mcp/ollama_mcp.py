#!/usr/bin/env python3
"""Minimal MCP server that exposes the local Ollama daemon to Claude Code.

Speaks the Model Context Protocol over stdio (JSON-RPC 2.0). Implements just
enough of the spec to advertise three tools that Claude Code can invoke:

  - ollama_chat        : chat-completion against any pulled model
  - ollama_list_models : show what's available locally
  - ollama_generate    : single-prompt completion (non-chat)

Environment:
  OLLAMA_BASE_URL — defaults to http://localhost:11434

Run standalone for a quick smoke test:
  echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp/ollama_mcp.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error


OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ollama"
SERVER_VERSION = "0.1.0"

TOOLS = [
    {
        "name": "ollama_chat",
        "description": (
            "Send a chat-style conversation to a model running on the local Ollama daemon "
            "and return the assistant's reply."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Ollama model tag (e.g. llama3.3, llama3.1:8b).",
                },
                "messages": {
                    "type": "array",
                    "description": "List of {role, content} messages.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role":    {"type": "string", "enum": ["system", "user", "assistant"]},
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                    },
                },
                "temperature": {"type": "number", "default": 0.7},
                "max_tokens":  {"type": "integer", "default": 512},
            },
            "required": ["model", "messages"],
        },
    },
    {
        "name": "ollama_generate",
        "description": "Single-prompt completion against a local Ollama model.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model":       {"type": "string"},
                "prompt":      {"type": "string"},
                "temperature": {"type": "number", "default": 0.7},
                "max_tokens":  {"type": "integer", "default": 512},
            },
            "required": ["model", "prompt"],
        },
    },
    {
        "name": "ollama_list_models",
        "description": "List models currently pulled on the local Ollama daemon.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _http(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{OLLAMA_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Ollama HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach Ollama at {OLLAMA_BASE}: {e.reason}. "
            "Is the daemon running? Try `ollama serve`."
        ) from e


def _tool_ollama_chat(args: dict) -> str:
    payload = {
        "model":       args["model"],
        "messages":    args["messages"],
        "temperature": args.get("temperature", 0.7),
        "max_tokens":  args.get("max_tokens", 512),
        "options":     {"num_predict": args.get("max_tokens", 512)},
        "stream":      False,
    }
    data = _http("POST", "/v1/chat/completions", payload)
    return data["choices"][0]["message"]["content"]


def _tool_ollama_generate(args: dict) -> str:
    payload = {
        "model":       args["model"],
        "prompt":      args["prompt"],
        "temperature": args.get("temperature", 0.7),
        "options":     {"num_predict": args.get("max_tokens", 512)},
        "stream":      False,
    }
    data = _http("POST", "/api/generate", payload)
    return data.get("response", "")


def _tool_ollama_list_models(_args: dict) -> str:
    data = _http("GET", "/api/tags")
    names = [m.get("name", "?") for m in data.get("models", [])]
    return "\n".join(names) if names else "(no models pulled)"


HANDLERS = {
    "ollama_chat":        _tool_ollama_chat,
    "ollama_generate":    _tool_ollama_generate,
    "ollama_list_models": _tool_ollama_list_models,
}


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle(message: dict) -> dict | None:
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    # Notifications (no id) get no response.
    if req_id is None and method not in ("initialize",):
        return None

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            return _err(req_id, -32601, f"Unknown tool: {name}")
        try:
            text = handler(args)
            return _ok(req_id, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            return _ok(req_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })

    if method == "ping":
        return _ok(req_id, {})

    return _err(req_id, -32601, f"Method not supported: {method}")


def main():
    # Line-delimited JSON-RPC over stdio.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = _handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
