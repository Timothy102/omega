# omega

A fast, small coding agent for your terminal. Bring your own models.

omega is a harness, not a model. It runs a tool-use loop against any
OpenAI-compatible endpoint, so you choose what drives it — open-weights models,
a hosted API, or a mix, with a different model for each job.

```
$ omega "why is the auth test failing?"
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
  `omega sessions`.
- **MCP, without the token cost.** Connect Linear, Notion, Sentry and friends.
  Their tools stay out of the prompt until the model searches for them —
  85 connected tools cost ~700 tokens instead of ~38,000.
- **Subagents.** Delegate wide searches to a cheaper model and get back a
  summary, so raw output never enters your main context. Their tool activity
  streams into your transcript as it happens; several run in parallel.
- **Context that doesn't fill up.** Any tool result over 4k chars is written to
  disk and the model sees a preview plus a `fetch_result` handle. Compaction
  exists but rarely triggers.
- **It can ask you things.** An `ask_user` tool blocks the turn on a real
  question with arrow-key options, instead of guessing.
- **Persistent memory.** A local knowledge graph (SQLite + FTS5), scoped per
  project and globally, with background consolidation.
- **A terminal UI.** Bare `omega` opens a full-screen TUI: transcript, live
  activity panel, status bar with token usage. `omega "prompt"` stays plain
  text for scripts and pipes.

## Install

Requires Python 3.11+, [ripgrep](https://github.com/BurntSushi/ripgrep), and
Node (only if you want MCP servers).

```bash
git clone https://github.com/Timothy102/omega.git && cd omega
uv tool install omega-code   # puts `omega` on your PATH; or `uv sync` to hack on it
```

## Setup

```bash
omega setup
```

Opens a local page in your browser to pick a provider, paste an API key,
choose a model for each role, and connect MCP servers. It measures each model's
latency so you can see what you're choosing.

Prefer a file? Write `~/.omega/config.json` yourself:

```json
{
  "providers": {
    "my-provider": {
      "baseUrl": "https://api.example.com/v1",
      "apiKeyEnv": "MY_API_KEY"
    },
    "anthropic": {
      "type": "anthropic",
      "apiKeyEnv": "ANTHROPIC_API_KEY"
    }
  },
  "models": {
    "opus":  { "model": "claude-opus-5",   "provider": "anthropic",  "context": 1048576, "effort": "high" },
    "small": { "model": "small-model",     "provider": "my-provider", "context": 128000 }
  },
  "roles": {
    "main":          { "alias": "opus" },
    "plan":          { "alias": "opus" },
    "subagent_fast": { "alias": "small" },
    "subagent_mid":  { "alias": "opus" },
    "compact":       { "alias": "small" },
    "memory":        { "alias": "small" }
  }
}
```

Use `apiKey` for a literal value or `apiKeyEnv` to read from the environment.
The file is written `0600`. A provider missing its key still loads fine — it
only fails, with a pointer to `omega setup` or the env var, when a role that
uses it actually runs.

A role is either an alias into `models` (above) or the older inline form
(`{ "model", "provider", "context" }`) — both work side by side.

### Roles

| role | what it does |
|---|---|
| `main` | drives your session — use your best model |
| `plan` | planning mode |
| `subagent_fast` | bounded lookups — use your quickest model |
| `subagent_mid` | reasoning across several files |
| `compact` | summarises old context when the window fills |
| `memory` | background consolidation of saved memory notes |

### Models

`providers[*].type` is `"openai"` (any OpenAI-compatible `/chat/completions`
endpoint — the default) or `"anthropic"` (the native Anthropic SDK, with
adaptive thinking, per-turn effort, prompt caching, and refusal fallbacks
built in). The built-in catalog:

| alias | model | provider | context |
|---|---|---|---|
| `fable` | `claude-fable-5-1` | anthropic | 1M |
| `opus` | `claude-opus-5` | anthropic | 1M |
| `sonnet` | `claude-sonnet-5` | anthropic | 1M |
| `haiku` | `claude-haiku-4-5` | anthropic | 200k |
| `kimi` | `moonshotai/kimi-k3` | openrouter-style | 1M |
| `glm` | `z-ai/glm-5.3-flash` | openrouter-style | 128k |

`omega models` prints the catalog with each role's current default.
`omega --model <alias-or-model-id>` overrides `main` and `plan` for the
session; `/model` in the TUI opens a picker (or takes an alias directly:
`/model sonnet`), and the status bar always shows the alias in use next to
the underlying model id.

## Usage

```bash
omega                              # interactive TUI
omega "fix the failing test"       # one-shot, plain output
echo "fix the failing test" | omega
omega --plan "add rate limiting"   # read-only: investigate and plan
omega --model sonnet "..."         # override main/plan for this session
omega --continue                   # resume this directory's last session
omega --resume 20260828-174247     # resume by id (a prefix works)
omega sessions                     # list sessions
omega models                       # show the model catalog and role defaults
omega memory gc                    # consolidate memory now
omega onboard                      # short terminal setup (no browser)
omega connections                  # manage MCP servers (see ## MCP)
omega "list my Linear issues"      # connects enabled MCP servers lazily
omega --mcp "..."                  # or connect everything eagerly at startup
omega --yolo "..."                 # skip permission prompts
omega eval run                     # headless task-suite scoring (see ## Eval harness)
omega resume [id]                  # resume a session (prefix works; no id -- pick from a list)
omega continue                     # resume this directory's last session
omega trace <id> [--tools] [--json]  # print a session's event trace (see ## Observability)
omega update                       # update omega to the latest release
omega doctor                       # check your environment and config
omega --version                    # print the version
omega --help                       # usage and flags
```

The first time omega runs with no `~/.omega/config.json`, or with no usable key
for `main`, it launches a small Textual wizard instead of exiting — pick a
provider, paste (or auto-detect) a key, pick a model, and it runs one real
turn live in the wizard to prove it works, then drops you straight into the
TUI. Piped or non-interactive invocations get the original plain `input()`
prompts instead. `omega setup` opens the fuller browser flow (multiple roles,
MCP servers, latency benchmarking) any time after.

In the TUI: `/plan` and `/build` switch modes, `/model` picks a model,
`/memory-gc` consolidates memory, `/quit` or ctrl-d exits, ctrl-c abandons
the current turn without losing the session, up/down walk input history,
ctrl-o opens the model picker. Permission prompts and `ask_user` questions
open as modals — arrow keys and enter, or type a free-text answer.

Session and edit-safety commands (TUI only):

- `/cost` — this session's tokens (in/out/cache) and USD, by model when more
  than one was used, priced from `omega.eval.prices`.
- `/export [path]` — writes the transcript as Markdown to `path`, or
  `~/.omega/sessions/<id>/transcript.md` by default, and prints where it went.
- `/compact` — forces compaction now instead of waiting for the token
  threshold, and shows the resulting note.
- `/undo [n]` — reverts the working tree to the checkpoint from `n` turns ago
  (default 1), after a y/n/always confirm.
- `/diff` — shows the working-tree diff since the last checkpoint in a modal.
- `/theme system|light|dark` — `system` (the default) paints nothing of its
  own, so omega takes the terminal's background, text colour and palette and
  looks light or dark along with it; `light` and `dark` force a painted
  palette instead. Remembered in `~/.omega/ui.json`.
- `/verify` — runs this project's auto-detected checks (tests/lint/types) and
  reports pass/fail per check.
- `/sessions` — lists this directory's other sessions in a modal; enter
  resumes the selected one in place, replacing the current history.

`/undo`, `/diff`, and `/verify` depend on `checkpoint.py`/`verify.py`; if
those aren't present in a build they print a dim "not available in this
build" instead of erroring.

## Context and artifacts

Every tool result is checked at dispatch: anything over 4,000 characters is
written to `~/.omega/sessions/<id>/artifacts/` and replaced in the
conversation with a head+tail preview and an id. The model calls
`fetch_result(id, offset, limit)` to page through the rest — so a huge test
log or `cat` costs a few hundred tokens of context, not thirty thousand.

The same store backs `save_artifact` / `update_artifact`, which let the model
build up a plan or report across a turn without re-emitting it each time, and
`list_artifacts` to see what's there.

## Permissions

Every tool call is classified before it runs:

- **allowed** — reads, searches, and writes inside your working directory
- **ask** — anything else, with `[y]es / [N]o / [a]lways` (`a` is remembered in
  `~/.omega/permissions.json`)
- **refused** — `sudo`, piping a download into a shell, force-pushes, and
  anything touching `~/.ssh`, `~/.aws`, or omega's own config

Content from MCP servers and from files outside your project is wrapped in
`<untrusted>` markers, and reading any of it downgrades `bash` to *ask* for the
rest of the turn — so a prompt injection in a ticket description can't quietly
reach your shell.

`--yolo` turns prompting off. Use it for scripts, not for exploring.

## MCP

`omega connections` manages MCP servers: a catalog of ~45 well-known ones
(Linear, Notion, GitHub, Postgres, Stripe, ...), whatever's already found in
your Claude Code config, and whatever you've configured yourself. Remote
servers proxy through `mcp-remote`, which owns the OAuth dance.

```bash
omega connections                    # table: name, state, tools, auth, source, last used
omega connections catalog            # browse the catalog by category
omega connections add linear         # configure a catalog entry
omega connections add mytool --cmd "npx -y my-mcp-server" --env API_KEY=...
omega connections connect linear     # connect now (triggers OAuth if needed)
omega connections test linear        # connect, report tool count, disconnect
omega connections enable|disable linear
omega connections remove linear
```

Connecting an OAuth server opens an authorize-me URL; `omega connections
connect` prints it and waits, so re-run it once you've clicked through.

Connected tools are *deferred*: they don't appear in the prompt at all. The
model calls `find_tools("linear issues")` to discover them and `call_tool` to
run one. Enabled servers connect **lazily** — the first `find_tools`/`call_tool`
of a session connects everything not yet connected, in parallel, with
failures recorded instead of raised. `omega --mcp` is still there for connecting
everything eagerly at startup instead.

Add your own server directly in `~/.omega/config.json` if you'd rather skip the
CLI:

```json
"mcp": {
  "linear": { "command": "npx", "args": ["-y", "mcp-remote@0.8.1", "https://mcp.linear.app/mcp"],
             "enabled": true, "catalog": "linear" }
}
```

`enabled` defaults to `true`; `catalog` is optional and just links the entry
back to its catalog metadata (auth type, category) for `omega connections`.

## Memory

The agent keeps a small local knowledge graph in SQLite (FTS5 full-text search
+ a graph of typed edges), in two scopes:

- **project** — `.omega/memory.db` next to the repo you're in; auto-gitignored
  the first time it's written, never committed
- **global** — `~/.omega/memory/memory.db`, shared across all projects

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
close once 5+ new nodes have accumulated, or on demand with `omega memory gc`
(`/memory-gc` in the REPL).

## Skills and project instructions

Two ways to steer the agent beyond a single prompt:

**Instructions** — an `OMEGA.md` (or `CLAUDE.md`, read as a fallback where no
`OMEGA.md` exists) is loaded once at startup and folded into the system
prompt: `~/.omega/OMEGA.md` (global) first, then every `OMEGA.md` from the
git root down to your working directory — so a monorepo subdir's file adds
to the root's instead of replacing it — then `.omega/instructions.md` if
present. Capped at 12,000 characters total, with a pointer to `read` the
source file for anything trimmed.

**Skills** — a `SKILL.md` (frontmatter `name` + `description`, then a
markdown checklist) is a sub-workflow the model loads on demand with the
`skill` tool and follows in the same conversation — not a subagent, so
nothing about the task is lost switching to it. omega reads the same format
Claude Code uses, so `~/.claude/skills/*` work here unchanged. Discovery
order (highest precedence first): `.omega/skills/*/SKILL.md` (project),
`~/.omega/skills/*/SKILL.md` (global), `~/.claude/skills/*/SKILL.md`. A
compact index (name + description) sits in the system prompt; `skill(name)`
fetches the full body, with any relative file links it contains rewritten to
absolute paths so `read` can follow them.

```bash
omega skills                # table: name, source, description
omega skills show <name>    # print a skill's body as the model sees it
```

## Eval harness

`omega eval` runs a suite of coding tasks headlessly against one or more
models and scores the results — the way to answer "did that prompt/config
change make things better or worse" with numbers instead of a vibe.

A task is a YAML file:

```yaml
name: version-flag
prompt: Add a --version flag to the CLI...
repo: .                 # path to run against (default: ".")
setup: git checkout -- . && git clean -fd   # optional, run before the agent
check: "uv run omega --version | grep -q 0.3"   # shell command, exit 0 = pass
timeout_s: 600           # default 600
mode: build               # build | plan (default build)
tags: [cli, smoke]
```

```bash
omega eval init                          # copy 3 example tasks into .omega/evals/
omega eval run                           # run .omega/evals/*.yaml against the `main` role
omega eval run --models opus,sonnet,spark --repeat 3 --jobs 4
omega eval run path/to/one-task.yaml --json
omega eval compare 20260901-101500 20260903-090000   # diff two runs
```

Each run happens in a throwaway copy of `repo` — a `git worktree` for a git
repo, a plain directory copy otherwise — never the repo you're actually
working in. `--yolo` semantics apply (no permission prompts). Per task ×
model × repeat, omega records pass/fail (from `check`'s exit code), model
rounds, tool calls by name, tokens in/out, an estimated cost from a
per-million-token price table (`omega/eval/prices.py`), wall time, and a
context telemetry manifest (per-round token/tool breakdown, system-prompt
size by zone, and an estimate-vs-actual token drift). Everything lands in
`.omega/evals/runs/<timestamp>/report.json`, plus a table on stdout.

## Observability

Every event a session's turns emit (`omega/events.py`) — tool calls, model
switches, compactions, checkpoints, verification, background jobs — is
appended as one JSON line to `~/.omega/sessions/<id>/trace.jsonl`, regardless
of what either UI chose to render. It's a second, independent sink: nothing
about the trace depends on the TUI or plain mode having shown that event.

```bash
omega trace <id>            # readable timeline: time offset, glyph, summary,
                             # tool durations, and per-turn token/cost totals
omega trace <id> --tools    # filter to just ToolStart/ToolEnd
omega trace <id> --json     # raw JSONL, one event object per line
```

Each line has the shape `{"t": <epoch>, "turn": <n>, "type": "ToolStart", ...}`
— `t` and `turn` plus every field of that event's dataclass, flattened. Costs
are computed from `omega.eval.prices`, matching the alias announced by the
most recent `ModelUsed` event in that turn.

`omega update` re-installs the current release with `uv tool install --force`
— from PyPI (`omega-code`) if that's how it was installed, or from
`git+https://github.com/Timothy102/omega.git@main` if it was installed from
git — then prints the freshly installed `omega --version`. `omega doctor`
checks Python ≥3.11, `rg`, `git`, `node`/`npx` (for MCP), `uv`, config
validity, each configured provider's key presence, and that
`~/.omega/config.json` is `0600`, as a ✓/✗ table.

## Development

Uses [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/),
and [mypy](https://mypy-lang.org/) in strict mode.

```bash
uv sync            # creates .venv with dev deps
uv run pytest
uv run ruff check
uv run mypy
```

## Where things live

```
~/.omega/config.json                    provider, models, MCP servers   (0600)
~/.omega/permissions.json               saved allow/deny rules
~/.omega/sessions/                      one JSON file per session
~/.omega/sessions/<id>/artifacts/       offloaded tool output + saved artifacts
~/.omega/sessions/<id>/trace.jsonl      per-event trace (see ## Observability)
~/.omega/sessions/<id>/transcript.md    `/export`'s default output path
~/.omega/sessions/<id>/checkpoints.json working-tree checkpoints (`/undo`, `/diff`)
~/.omega/memory/memory.db               global memory (SQLite + FTS5)
<project>/.omega/memory.db              project memory (gitignored)
~/.omega/history                        REPL input history
~/.omega/OMEGA.md                       global instructions
<project>/.omega/instructions.md        project instructions (local-only)
<project>/OMEGA.md                      project instructions (shared, committed)
~/.omega/skills/*/SKILL.md              global skills
<project>/.omega/skills/*/SKILL.md      project skills
```

Sessions contain full transcripts, including file contents and command output.
They're local, but treat them as sensitive.

## Status

Early. It works and it's tested, but expect rough edges. Known gaps: sessions
rewrite the whole file each turn (fine for now, will become append-only);
compaction, when it does trigger, replaces old messages rather than archiving
them; artifacts and sessions are never garbage-collected; and the TUI has
been exercised on macOS terminals only.

## Licence

MIT
