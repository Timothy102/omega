from dataclasses import dataclass
from typing import Any

from .. import compact, events
from ..session import Message

VOLATILE_MARKER = "<!-- volatile -->"


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    duration_s: float
    result_chars: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "duration_s": round(self.duration_s, 3),
                "result_chars": self.result_chars}


@dataclass(frozen=True)
class RoundRecord:
    index: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cache_tokens: int
    estimated_tokens: int
    tool_calls: tuple[ToolCallRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens, "cache_tokens": self.cache_tokens,
            "estimated_tokens": self.estimated_tokens,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
        }


@dataclass(frozen=True)
class DriftPoint:
    round: int
    estimated: int
    actual: int

    @property
    def drift(self) -> int:
        return self.actual - self.estimated

    @property
    def drift_pct(self) -> float | None:
        return (self.drift / self.actual) if self.actual else None

    def to_dict(self) -> dict[str, Any]:
        return {"round": self.round, "estimated": self.estimated, "actual": self.actual,
                "drift": self.drift, "drift_pct": self.drift_pct}


@dataclass(frozen=True)
class SystemZones:
    fixed_chars: int
    volatile_chars: int

    def to_dict(self) -> dict[str, Any]:
        return {"fixed_chars": self.fixed_chars, "volatile_chars": self.volatile_chars}


@dataclass(frozen=True)
class ContextManifest:
    rounds: tuple[RoundRecord, ...]
    system_zones: SystemZones
    drift: tuple[DriftPoint, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rounds": [r.to_dict() for r in self.rounds],
            "system_zones": self.system_zones.to_dict(),
            "drift": [d.to_dict() for d in self.drift],
        }


def split_system_zones(system: str, marker: str = VOLATILE_MARKER) -> SystemZones:
    if marker not in system:
        return SystemZones(fixed_chars=len(system), volatile_chars=0)
    fixed, _, volatile = system.partition(marker)
    return SystemZones(fixed_chars=len(fixed), volatile_chars=len(volatile))


def _cache_tokens(usage: events.Usage) -> int:
    # `cached_tokens`/`cache_read` may not exist on every Usage build -- A2 is
    # adding cache fields to this event independently of this module.
    return int(getattr(usage, "cached_tokens", None) or getattr(usage, "cache_read", None) or 0)


def build_manifest(evs: list[events.Event], system: str, schemas: list[dict[str, Any]],
                   initial_history: list[Message], final_history: list[Message]) -> ContextManifest:
    """Reconstructs per-round token/tool telemetry from the events emitted by
    `loop.run_agent` plus the `history` list it mutates in place. Each round
    appends exactly 1 (assistant) + len(tool_calls) messages to `history`
    before the next request goes out -- except the final round (no tool
    calls), which ends in `Done` instead of `Usage` and so has no actual
    `prompt_tokens` in the current event schema; its drift point is skipped,
    though the round itself is still recorded with `prompt_tokens=None`."""
    overhead = compact.estimate_tokens([{"role": "system", "content": system}]) \
        + compact.estimate_tokens(schemas)

    rounds: list[RoundRecord] = []
    drift: list[DriftPoint] = []
    msg_idx = len(initial_history)
    round_calls: list[ToolCallRecord] = []
    round_index = -1

    for ev in evs:
        if isinstance(ev, events.Phase) and ev.state == "waiting":
            round_index += 1
            round_calls = []
        elif isinstance(ev, events.ToolEnd):
            round_calls.append(ToolCallRecord(ev.name, ev.duration_s, ev.result_chars))
        elif isinstance(ev, events.Usage):
            estimated = overhead + compact.estimate_tokens(final_history[:msg_idx])
            rounds.append(RoundRecord(round_index, ev.prompt_tokens, ev.completion_tokens,
                                      _cache_tokens(ev), estimated, tuple(round_calls)))
            if ev.prompt_tokens:
                drift.append(DriftPoint(round_index, estimated, ev.prompt_tokens))
            msg_idx += 1 + len(round_calls)
        elif isinstance(ev, events.Done):
            estimated = overhead + compact.estimate_tokens(final_history[:msg_idx])
            rounds.append(RoundRecord(round_index, None, None, 0, estimated, tuple(round_calls)))

    return ContextManifest(tuple(rounds), split_system_zones(system), tuple(drift))
