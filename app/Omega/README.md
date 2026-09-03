# Omega.app

A native macOS SwiftUI client for the `omega` coding-agent daemon (`omega serve`).

## Layout

```
app/Omega/
  project.yml                 xcodegen project spec
  Omega.xcodeproj              generated — do not hand-edit, regenerate instead
  Packages/OmegaCore/          pure Swift package: models, Event decoding, OmegaClient, TranscriptModel reducer
  App/                         app entry point, root navigation, app-wide state, daemon-setup screen
  Views/{Sidebar,Overview,Task,Inspector,Terminal,Settings}/
  Models/                      per-task view model
  Services/                    daemon launcher, notifications, app settings
```

## Build / run

Requires Xcode 26+, Swift 6 toolchain, and [xcodegen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`).

```sh
cd app/Omega
xcodegen generate
xcodebuild -project Omega.xcodeproj -scheme Omega -configuration Debug build
```

Or open `Omega.xcodeproj` in Xcode and hit Run. On launch the app looks for `~/.omega/serve.json`
and probes `/api/health`; if that fails it tries to spawn `omega serve` itself (searching
`~/.local/bin`, Homebrew paths, and `$PATH`). If `omega` isn't installed you'll see a setup
screen with `uv tool install omega-code` and a Copy button.

### One-time machine setup

SwiftTerm ships a Swift Package Manager **build plugin** (`SwiftTermBuildInfoPlugin`). Xcode's
CLI refuses to run any package plugin until it's been explicitly trusted, and there is no
non-interactive flag on the exact build command required by this project's spec
(`xcodebuild -project ... -scheme Omega -configuration Debug build`, no extra flags) to
pre-approve it. Run once per machine:

```sh
defaults write com.apple.dt.Xcode IDESkipPackagePluginFingerprintValidatation -bool YES
```

(Alternatively pass `-skipPackagePluginValidation` to any one-off `xcodebuild` invocation instead
of setting this globally.) SwiftTerm also compiles a Metal shader; if `xcodebuild` fails with
`cannot execute tool 'metal' due to missing Metal Toolchain`, run:

```sh
xcodebuild -downloadComponent MetalToolchain
```

### Tests

```sh
xcodebuild test -scheme Omega -destination 'platform=macOS'
```

Runs `OmegaCoreTests` (24 tests) — no daemon required. The transcript reducer
(`TranscriptModel`) is the highest-value suite: it's scripted against sequences of `Event`s and
asserts the exact block/grouping/dedupe/collapse behavior described below, independent of any
SwiftUI code. `OmegaClientProtocol` lets the app (and tests/previews) swap in
`MockOmegaClient` instead of hitting a real daemon.

## The daemon contract this client was built against

Phase 9 of the plan doc specifies REST/WS **paths**; a lot of the request/response shapes below
were this client's own choice while `omega serve` didn't exist yet, and were later **confirmed or
corrected directly by the daemon author** once it landed. Anything still marked "assumption" below
is unconfirmed.

### `~/.omega/serve.json` (confirmed)
```json
{"port": 7777, "token": "...", "pid": 1234}
```
No `host` field — the daemon binds loopback-only, so the client always uses `127.0.0.1`.

### REST (`http://127.0.0.1:{port}/api/...`, `Authorization: Bearer {token}` except `/health`)

| Method | Path | Body → Response |
|---|---|---|
| GET | `/health` | no auth → `{"status":"ok","version"}` |
| GET | `/tasks` | → `{"tasks": [Task]}` |
| POST | `/tasks` | `{repo, prompt?, worktree?, model?, mode?}` → `Task` (starts running immediately if `prompt` is given) |
| GET | `/tasks/{id}` | → `Task` |
| GET | `/tasks/{id}/trace` | → `{"trace": "<NDJSON string, one Event per line>"}` — used for history replay when opening a task; there is no separate `/events` endpoint |
| POST | `/tasks/{id}/prompt` | `{text, mode?}` → `{"ok": true}` *(assumption)* |
| POST | `/tasks/{id}/cancel` | → `{"ok": true}` *(assumption)* |
| POST | `/tasks/{id}/answer` | `{request_id, answer: String}` → `{"ok": true}` *(answer is the chosen label(s) joined by `", "`, or free text — not a list)* |
| POST | `/tasks/{id}/confirm` | `{request_id, allow: Bool, always: Bool}` → `{"ok": true}` |
| POST | `/tasks/{id}/model` | `{model}` → `{"ok": true}` *(assumption)* |
| POST | `/tasks/{id}/mode` | `{mode}` → `{"ok": true}` *(assumption)* |
| POST | `/tasks/{id}/undo` | → `{"ok": true}` *(assumption)* |
| GET | `/tasks/{id}/git` | → `GitRepoState` *(assumption)* |
| GET | `/tasks/{id}/diff?path=` | → `{path, diff}` *(assumption)* |
| GET | `/tasks/{id}/jobs` | → `{"jobs": [BackgroundJob]}` — the agent's background bash jobs; exact field names beyond `id`/`command`/`status`/`exit_code` are a guess mirroring `JobStarted`/`JobFinished` |
| POST | `/tasks/{id}/pr` | `{title?, body?}` → `PullRequest` *(assumption)* |
| GET | `/tasks/{id}/artifacts` | → `{"artifacts": [Artifact]}` *(assumption)* |
| GET | `/tasks/{id}/artifacts/{aid}` | → `Artifact` *(assumption)* |
| GET | `/models` | → `{"models": [ModelInfo]}` *(assumption)* |
| GET | `/connections` | → `{"connections": [Connection]}` *(assumption)* |
| POST | `/connections/{name}/connect` | → `{"ok": true}` *(assumption)* |
| GET | `/terminals` | → `{"terminals": [Terminal]}` *(assumption — list shape)* |
| POST | `/terminals` | `{task_id?, cwd?}` → `{id, pid, cwd, created}` (confirmed minimal shape — `task_id`/`title`/`last_line` are this client's own optional additions for the sidebar, and may simply be absent) |
| DELETE | `/terminals/{id}` | → `{"ok": true}` *(assumption)* |

All JSON keys are `snake_case`, matching `events.py`'s own field names (`call_id`, `args_preview`,
etc.) — the client decodes with explicit `CodingKeys`, not a blanket `convertFromSnakeCase`, so any
daemon-side rename needs a matching client change.

### WebSocket

- **`/ws/tasks/{id}` (header or `?token=`)** — every `omega/events.py` event as
  `{"type": "<EventClassName>", ...fields, "t": <unix seconds>, "turn": <n>}` (confirmed: `type` is
  the Python dataclass name verbatim). Two additional message types are **not** in `events.py` —
  the round-trip `ask_user`/permission-confirm flow — confirmed to carry `request_id` and
  **no** `t`/`turn`; their other fields are still this client's own guess:
  - `{"type": "ask_user_request", "request_id", "question", "header"?, "options": [{"label","description"?}], "multi_select"}`
  - `{"type": "confirm_request", "request_id", "tool_name", "args_preview", "risk"?}`
- **`/ws/terminals/{id}`** — binary WS frames are raw PTY bytes both directions; client→server resize is a **text** JSON frame `{"resize":[cols,rows]}` *(assumption)*.
- **`/ws/overview`** — `{"type":"tasks","tasks":[Task]}` / `{"type":"terminals","terminals":[Terminal]}` pushed on change *(assumption)*.

### Event → Swift case mapping (`Packages/OmegaCore/Sources/OmegaCore/Events.swift`)

`TextDelta`, `ToolStart` (`call_id, name, args_preview, subagent_id?, tier?`), `ToolEnd`
(`call_id, name, result_preview, duration_s, offloaded, artifact_id?, result_chars, outcome`),
`Compacted`, `MemoryWrite`, `MemoryConsolidated`, `SubagentSpawned`, `SubagentDone`, `Error`,
`Done`, `Usage`, `Fallback`, `ModelUsed`, `Phase` (`state: waiting|thinking|streaming|tools|idle`),
`Checkpoint`, `Verified`, `JobStarted`, `JobFinished`, `RetryBlocked`, plus the two synthetic
`ask_user_request`/`confirm_request` messages above. `ToolStart.args_preview` is trusted to be the
**fully pre-formatted** line (`"read  foo.py"`, `"bash  $ pytest"`, …) per every example in
`omega/ui/format.py`'s `describe_call()` — the client renders `"● " + args_preview"` directly
rather than re-deriving a padded-name column, since the exact split between "name" and "detail"
in the TUI's own line template couldn't be confirmed from the source alone.

## Transcript reducer — deliberate deviation from the Python TUI

`omega/ui/tui/transcript.py`'s actual reducer keeps ONE assistant text block open across an
entire turn, interleaving tool calls into it, and only renders it as Markdown once at `Done`. This
client instead closes the open assistant-text block on every `ToolStart`/`SubagentSpawned` and
opens a fresh one after the next `TextDelta` — i.e. text/tools alternate as separate blocks, matching
this project's brief (and how Claude Code's own transcript reads) rather than the TUI's current
behavior. Everything else mirrors the TUI: tool-call groups collapse past 3 rows, consecutive
identical calls (same name + args_preview) fold into one row with a `×N` suffix instead of
appending, subagent tool calls nest under their spawning row instead of joining the top-level
group, and `SubagentDone` updates that row in place rather than appending a new one.

## Known gaps / follow-ups

- `DELETE /api/terminals/{id}` is this client's own addition — nothing in the plan doc calls for
  terminal teardown, but closing a terminal tab should free the daemon-side PTY.
- The literal spinner glyph `✻` from the task brief doesn't appear anywhere in the actual TUI code
  (which uses a 10-frame braille spinner); the app's status line treats it as illustrative and
  picks its own SwiftUI spinner.
