import pytest

from omega import artifacts


@pytest.fixture(autouse=True)
def isolate_artifacts_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "DIR", tmp_path)
    yield


def test_save_fetch_round_trip():
    sid = "sess1"
    artifact_id = artifacts.save(sid, "hello world", title="greeting")
    assert artifacts.fetch(sid, artifact_id) == "hello world"


def test_fetch_missing_artifact_returns_error_string():
    result = artifacts.fetch("sess1", "deadbeef")
    assert "error" in result.lower()


def test_offload_if_large_under_threshold_returns_unchanged():
    text = "x" * 100
    assert artifacts.offload_if_large(text, "sess1", threshold=4000) == text


def test_offload_if_large_over_threshold_saves_and_returns_preview():
    text = "y" * 5000
    result = artifacts.offload_if_large(text, "sess1", threshold=4000, preview_limit=1200)
    assert result != text
    assert "full output: 5000 chars" in result
    assert "fetch_result(" in result

    marker = "saved as artifact "
    start = result.index(marker) + len(marker)
    artifact_id = result[start:].split(" ", 1)[0].rstrip(".—-")
    fetched = artifacts.fetch("sess1", artifact_id, limit=len(text))
    assert fetched == text


def test_offload_if_large_at_exact_threshold_returns_unchanged():
    text = "z" * 4000
    assert artifacts.offload_if_large(text, "sess1", threshold=4000) == text


def test_update_replaces_content_and_bumps_char_count():
    sid = "sess1"
    artifact_id = artifacts.save(sid, "short", title="t")
    artifacts.update(sid, artifact_id, "a much longer replacement body")
    assert artifacts.fetch(sid, artifact_id) == "a much longer replacement body"

    import json
    meta_path = artifacts._session_dir(sid) / f"{artifact_id}.meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["char_count"] == len("a much longer replacement body")
    assert meta["updated"] >= meta["created"]


def test_update_missing_artifact_returns_error_string():
    result = artifacts.update("sess1", "deadbeef", "content")
    assert "error" in result.lower()


def test_list_artifacts_reports_id_title_kind_size_created():
    sid = "sess1"
    a1 = artifacts.save(sid, "abc", title="first", kind="offload")
    a2 = artifacts.save(sid, "abcdef", title="second", kind="authored")
    rows = {r["id"]: r for r in artifacts.list_artifacts(sid)}

    assert set(rows) == {a1, a2}
    assert rows[a1]["title"] == "first"
    assert rows[a1]["kind"] == "offload"
    assert rows[a1]["size"] == 3
    assert rows[a2]["kind"] == "authored"
    assert rows[a2]["size"] == 6
    assert all("created" in r for r in rows.values())


def test_list_artifacts_empty_session_returns_empty_list():
    assert artifacts.list_artifacts("no-such-session") == []


def test_fetch_offset_and_limit_slicing():
    sid = "sess1"
    content = "0123456789" * 10
    artifact_id = artifacts.save(sid, content)

    assert artifacts.fetch(sid, artifact_id, offset=0, limit=10) == content[:10]
    assert artifacts.fetch(sid, artifact_id, offset=10, limit=10) == content[10:20]
    assert artifacts.fetch(sid, artifact_id, offset=95, limit=10) == content[95:]


def test_artifacts_from_different_sessions_are_isolated():
    a1 = artifacts.save("sess1", "content one")
    a2 = artifacts.save("sess2", "content two")
    assert artifacts.fetch("sess1", a2).lower().startswith("error")
    assert artifacts.fetch("sess2", a1).lower().startswith("error")
    assert artifacts.fetch("sess1", a1) == "content one"
    assert artifacts.fetch("sess2", a2) == "content two"
