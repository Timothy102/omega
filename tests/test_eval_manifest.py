from omega import compact, events
from omega.eval import manifest


def test_split_system_zones_with_marker():
    z = manifest.split_system_zones("fixed part<!-- volatile -->volatile part")
    assert z.fixed_chars == len("fixed part")
    assert z.volatile_chars == len("volatile part")


def test_split_system_zones_without_marker():
    z = manifest.split_system_zones("just fixed, no marker")
    assert z.fixed_chars == len("just fixed, no marker") and z.volatile_chars == 0


def test_build_manifest_records_rounds_and_drift():
    system = "sys prompt"
    schemas: list = []
    initial_history = [{"role": "user", "content": "hi"}]
    final_history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file contents"},
        {"role": "assistant", "content": "done"},
    ]
    evs: list[events.Event] = [
        events.Phase("waiting"),
        events.ToolStart(call_id="c1", name="read", args_preview="read x"),
        events.ToolEnd(call_id="c1", name="read", result_preview="ok", duration_s=0.5,
                       offloaded=False, result_chars=13),
        events.Usage(prompt_tokens=42, completion_tokens=5, used=47, limit=100000),
        events.Phase("waiting"),
        events.Done(text="done"),
    ]

    m = manifest.build_manifest(evs, system, schemas, initial_history, final_history)

    assert len(m.rounds) == 2
    r0 = m.rounds[0]
    assert r0.prompt_tokens == 42 and r0.completion_tokens == 5
    assert len(r0.tool_calls) == 1
    assert r0.tool_calls[0].name == "read" and r0.tool_calls[0].result_chars == 13

    r1 = m.rounds[1]
    assert r1.prompt_tokens is None and r1.tool_calls == ()

    assert len(m.drift) == 1
    d = m.drift[0]
    assert d.round == 0 and d.actual == 42
    expected_estimate = (compact.estimate_tokens([{"role": "system", "content": system}])
                        + compact.estimate_tokens(schemas)
                        + compact.estimate_tokens(initial_history))
    assert d.estimated == expected_estimate
    assert d.drift == 42 - expected_estimate


def test_build_manifest_no_usage_events_yields_no_drift():
    system = "sys"
    evs: list[events.Event] = [events.Phase("waiting"), events.Done(text="ok")]
    m = manifest.build_manifest(evs, system, [], [{"role": "user", "content": "hi"}],
                                [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}])
    assert m.drift == ()
    assert len(m.rounds) == 1 and m.rounds[0].prompt_tokens is None


def test_manifest_to_dict_is_json_shaped():
    system = "sys"
    m = manifest.build_manifest([events.Phase("waiting"), events.Done(text="ok")], system, [],
                                [{"role": "user", "content": "hi"}],
                                [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}])
    d = m.to_dict()
    assert set(d) == {"rounds", "system_zones", "drift"}
    assert d["system_zones"]["fixed_chars"] == len(system)
