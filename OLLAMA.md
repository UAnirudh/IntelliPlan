# Ollama integration

IntelliPlan can run its chatbot, Plani Tutor, and the multilingual safety
pipeline against a local Ollama daemon instead of Groq. The same instance is
also exposed to Claude Code through an MCP server, so the CLI can call Llama
locally for its own tasks.

## 1. Install Ollama and pull models

```bash
# Install (macOS)
brew install ollama
# or Windows / Linux: https://ollama.com/download

# Start the daemon (binds to http://localhost:11434 by default)
ollama serve

# Pull the models IntelliPlan uses
ollama pull llama3.3              # main tutor + chatbot
ollama pull llama3.1:8b           # fast moderation model
```

## 2. Point IntelliPlan at Ollama

Add to your project `.env`:

```bash
OLLAMA_BASE_URL=http://localhost:11434
# Optional: override the default Groq→Ollama tag map
OLLAMA_MODEL_MAP={"llama-3.3-70b-versatile":"llama3.3","llama-3.1-8b-instant":"llama3.1:8b"}
```

When `OLLAMA_BASE_URL` is set, `chatbot_api._llm_chat()` routes every call —
the tutor, Plani, input moderation, output moderation — to Ollama's
OpenAI-compatible `/v1/chat/completions` endpoint. Unset the variable to fall
back to Groq.

No code change required; the wrapper does the routing.

## 3. Expose Ollama to Claude Code itself

The repo ships a minimal MCP server at [`mcp/ollama_mcp.py`](mcp/ollama_mcp.py)
and a project-level `.claude/settings.json` that registers it. With those in
place, Claude Code surfaces three tools:

| Tool                  | Purpose                                  |
|-----------------------|------------------------------------------|
| `ollama_chat`         | Chat-style completion against any local model |
| `ollama_generate`     | Single-prompt completion                 |
| `ollama_list_models`  | List models pulled locally               |

Inside a Claude Code session, run `/mcp` to confirm the `ollama` server is
connected. Claude can then call e.g. `ollama_chat` with `{"model":"llama3.3","messages":[…]}`
and use the result inline — same chain IntelliPlan uses in production.

### Quick smoke test

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp/ollama_mcp.py
```

You should see the three tool descriptors returned. If Ollama is running,
`tools/call` with `ollama_list_models` will print your local model tags.

## 4. Troubleshooting

- **`Could not reach Ollama at http://localhost:11434`** — make sure
  `ollama serve` is running and the port matches `OLLAMA_BASE_URL`.
- **Slow first response** — Ollama lazy-loads weights on the first request;
  subsequent calls are fast. Use a smaller model (`llama3.1:8b`) for the
  moderation path on lower-RAM machines.
- **Groq still being used** — confirm `OLLAMA_BASE_URL` is in the *active*
  environment (the Flask process picks it up from `.env` via `load_dotenv()`,
  but a stale process needs a restart).
