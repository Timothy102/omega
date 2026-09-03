# rig

A fast, small coding agent for your terminal. Bring your own models.

rig is a harness, not a model. It runs a tool-use loop against any
OpenAI-compatible endpoint, so you choose what drives it — open-weights models,
a hosted API, or a mix, with a different model for each job.

```
$ rig "why is the auth test failing?"
⏺ bash   pytest tests/test_auth.py -x
⏺ read   src/auth.py
The test asserts a 401 but `verify_token` returns 403 for an expired
token — src/auth.py:88 raises Forbidden instead of Unauthorized.
```

## What's in it

- **Parallel + streaming tool dispatch.** Tool calls execute *while* the model
  is still generating the next one, not after the response closes.
- **Planning mode.** `--plan` gives the model read-only tools and asks for a
  plan. The restriction is enforced at dispatch, not just hidden from the schema.
- **A permissions layer.** Read-only commands run freely; anything that can
  change your machine asks first; a small set of things is refused outright.
- **Sessions.** Every turn is saved. Resume with `--continue`, list with
  `rig sessions`.
- **MCP, without the token cost.** Connect Linear, Notion, Sentry and friends.
  Their tools stay out of the prompt until the model searches for them —
  85 connected tools cost ~700 tokens instead of ~38,000.
- **Subagents.** Delegate wide searches to a cheaper model and get back a
  summary, so raw output never enters your main context.
- **Persistent memory.** A local knowledge graph (SQLite + FTS5), scoped per
  project and globally, with background consolidation.

## Install

Requires Python 3.11+, [ripgrep](https://github.com/BurntSushi/ripgrep), and
Node (only if you want MCP servers).

```bash
git clone https://github.com/Timothy102/rig.git && cd rig
uv tool install .       # puts `rig` on your PATH; or `uv sync` to hack on it
```

## Setup

```bash
rig setup
```

Opens a local page in your browser to pick a provider, paste an API key,
choose a model for each role, and connect MCP servers. It measures each model's
latency so you can see what you're choosing.

Prefer a file? Write `~/.rig/config.json` yourself:

```json
{
  "providers": {
    "my-provider": {
      "baseUrl": "https://api.example.com/v1",
      "apiKeyEnv": "MY_API_KEY"
    }
  },
  "roles": {
    "main":          { "model": "big-model",   "provider": "my-provider", "context": 200000 },
    "plan":          { "model": "big-model",   "provider": "my-provider", "context": 200000 },
    "subagent_fast": { "model": "small-model", "provider": "my-provider", "context": 128000 },
    "subagent_mid":  { "model": "big-model",   "provider": "my-provider", "context": 200000 },
    "compact":       { "model": "small-model", "provider": "my-provider", "context": 128000 },
    "memory":        { "model": "small-model", "provider": "my-provider", "context": 128000 }
  }
}
```

Use `apiKey` for a literal value or `apiKeyEnv` to read from the environment.
The file is written `0600`.

### Roles

| role | what it does |
|---|---|
| `main` | drives your session — use your best model |
| `plan` | planning mode |
| `subagent_fast` | bounded lookups — use your quickest model |
| `subagent_mid` | reasoning across several files |
| `compact` | summarises old context when the window fills |
| `memory` | background consolidation of saved memory notes |

## Usage

```bash
rig                              # interactive
rig "fix the failing test"       # one-shot
rig --plan "add rate limiting"   # read-only: investigate and plan
rig --continue                   # resume this directory's last session
rig --resume 20260828-174247     # resume by id (a prefix works)
rig sessions                     # list sessions
rig --mcp "list my Linear issues"
rig --yolo "..."                 # skip permission prompts
```

In the REPL: `/plan` and `/build` switch modes, ctrl-d exits, ctrl-c abandons
the current turn without losing the session.

## Permissions

Every tool call is classified before it runs:

- **allowed** — reads, searches, and writes inside your working directory
- **ask** — anything else, with `[y]es / [N]o / [a]lways` (`a` is remembered in
  `~/.rig/permissions.json`)
- **refused** — `sudo`, piping a download into a shell, force-pushes, and
  anything touching `~/.ssh`, `~/.aws`, or rig's own config

Content from MCP servers and from files outside your project is wrapped in
`<untrusted>` markers, and reading any of it downgrades `bash` to *ask* for the
rest of the turn — so a prompt injection in a ticket description can't quietly
reach your shell.

`--yolo` turns prompting off. Use it for scripts, not for exploring.

## MCP

rig imports MCP servers from your Claude Code config and from installed
plugins, and proxies remote ones through `mcp-remote` so OAuth works.

```bash
rig --mcp "what's in my Linear backlog?"
```

Connected tools are *deferred*: they don't appear in the prompt at all. The
model calls `find_tools("linear issues")` to discover them and `call_tool` to
run one. Add your own in `~/.rig/config.json`:

```json
"mcp": {
  "linear": { "command": "npx", "args": ["-y", "mcp-remote@0.8.1", "https://mcp.linear.app/mcp"] }
}
```

## Memory

The agent keeps a small local knowledge graph in SQLite (FTS5 full-text search
+ a graph of typed edges), in two scopes:

- **project** — `.rig/memory.db` next to the repo you're in; auto-gitignored
  the first time it's written, never committed
- **global** — `~/.rig/memory/memory.db`, shared across all projects

Nodes have a `type` (`fact`, `preference`, `decision`, `entity`, `file_note`,
`open_question`), a confidence, a volatility, and an importance, which
together decide what gets auto-injected into the system prompt each session
vs. what stays recall-only.

Tools: `remember` saves a node; `recall` searches both scopes and expands
related nodes; `supersede` replaces an outdated node while keeping the old
one queryable; `link` adds an explicit relation (`contradicts`, `depends_on`,
`part_of`, ...) between two existing nodes. A regex safety net forces
`sensitivity="sensitive"` on anything that looks like a secret or PII,
regardless of what the model passed.

A background pass (the `memory` role) periodically merges near-duplicates,
flags contradictions, and retags stale entries — automatically at session
close once 5+ new nodes have accumulated, or on demand with `rig memory gc`
(`/memory-gc` in the REPL).

## Development

Uses [uv](https://docs.astral.sh/uv/) and [ruff](https://docs.astral.sh/ruff/).

```bash
uv sync            # creates .venv with dev deps
uv run pytest
uv run ruff check
```

## Where things live

```
~/.rig/config.json                    provider, models, MCP servers   (0600)
~/.rig/permissions.json               saved allow/deny rules
~/.rig/sessions/                      one JSON file per session
~/.rig/sessions/<id>/artifacts/       offloaded tool output + saved artifacts
~/.rig/memory/memory.db               global memory (SQLite + FTS5)
<project>/.rig/memory.db              project memory (gitignored)
~/.rig/history                        REPL input history
```

Sessions contain full transcripts, including file contents and command output.
They're local, but treat them as sensitive.

## Status

Early. It works and it's tested, but expect rough edges. Known gaps: sessions
rewrite the whole file each turn (fine for now, will become append-only), and
compaction replaces old messages rather than archiving them.

## Licence

MIT
