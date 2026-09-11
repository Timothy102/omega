from omega.ui.composer import (
    build_prompt,
    display_path,
    insert_mention,
    mention_query,
    rank_files,
)


def test_mention_query_at_cursor():
    q = mention_query("see @src/app", len("see @src/app"))
    assert q is not None
    assert q.query == "src/app"
    assert q.start == 4


def test_mention_query_empty_after_at():
    q = mention_query("hi @", 4)
    assert q is not None
    assert q.query == ""


def test_mention_query_rejects_email():
    assert mention_query("write user@host.com", len("write user@host.com")) is None


def test_mention_query_rejects_after_space():
    assert mention_query("see @foo bar", len("see @foo bar")) is None


def test_rank_prefers_filename_prefix():
    files = [
        "src/unrelated/compactor/notes.md",
        "src/ComposerView.swift",
        "app/Foo.swift",
        "README.md",
    ]
    ranked = rank_files(files, "comp")
    assert ranked[0] == "src/ComposerView.swift"
    assert ranked[-1] == "src/unrelated/compactor/notes.md"


def test_rank_empty_query_prefers_shallow():
    files = ["src/a/b/c.swift", "README.md", "app/Foo.swift"]
    assert rank_files(files, "")[0] == "README.md"


def test_insert_mention_replaces_token():
    text, cursor = insert_mention("look at @Com", len("look at @Com"), "src/ComposerView.swift")
    assert text == "look at `src/ComposerView.swift` "
    assert cursor == len(text)


def test_build_prompt_lists_attachments():
    assert build_prompt("fix this", ["/tmp/a.png", "src/Foo.swift"]) == (
        "fix this\n\nAttached:\n- /tmp/a.png\n- src/Foo.swift"
    )
    assert build_prompt("  hello  ", []) == "hello"


def test_display_path_relativizes():
    root = "/Users/tim/code/omega"
    assert display_path("/Users/tim/code/omega/app/Foo.swift", root) == "app/Foo.swift"
    assert display_path("/tmp/x.png", root) == "/tmp/x.png"
