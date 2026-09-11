import Foundation
import Testing
@testable import OmegaCore

@Test func mentionQueryFindsTokenAtCursor() {
    let text = "see @src/app"
    let q = ComposerMentions.query(in: text, cursorUTF16: (text as NSString).length)
    #expect(q?.query == "src/app")
    #expect(q?.location == 4)
    #expect(q?.length == 8)
}

@Test func mentionQueryEmptyRightAfterAt() {
    let text = "hi @"
    let q = ComposerMentions.query(in: text, cursorUTF16: (text as NSString).length)
    #expect(q?.query == "")
    #expect(q?.location == 3)
}

@Test func mentionQueryNilForEmail() {
    let text = "write user@host.com"
    let q = ComposerMentions.query(in: text, cursorUTF16: (text as NSString).length)
    #expect(q == nil)
}

@Test func mentionQueryNilAfterSpace() {
    let text = "see @foo bar"
    let q = ComposerMentions.query(in: text, cursorUTF16: (text as NSString).length)
    #expect(q == nil)
}

@Test func mentionQueryMidToken() {
    let text = "look at @ComposerView.swift please"
    let at = (text as NSString).range(of: "@").location
    let cursor = at + 4 // "@Com|"
    let q = ComposerMentions.query(in: text, cursorUTF16: cursor)
    #expect(q?.query == "Com")
    #expect(q?.location == at)
}

@Test func rankPrefersFilenamePrefix() {
    let files = [
        "src/unrelated/compactor/notes.md",
        "src/ComposerView.swift",
        "app/Foo.swift",
        "README.md",
    ]
    let ranked = ComposerMentions.rank(files: files, query: "comp", limit: 10)
    #expect(ranked.first == "src/ComposerView.swift")
    #expect(ranked.last == "src/unrelated/compactor/notes.md")
}

@Test func rankEmptyQueryPrefersShallowPaths() {
    let files = ["src/a/b/c.swift", "README.md", "app/Foo.swift"]
    let ranked = ComposerMentions.rank(files: files, query: "", limit: 10)
    #expect(ranked.first == "README.md")
}

@Test func promptBuilderPassthroughWhenNoAttachments() {
    #expect(ComposerPromptBuilder.build(text: "  hello  ", attachmentPaths: []) == "hello")
}

@Test func promptBuilderListsAttachments() {
    let body = ComposerPromptBuilder.build(text: "fix this", attachmentPaths: ["/tmp/a.png", "src/Foo.swift"])
    #expect(body == "fix this\n\nAttached:\n- /tmp/a.png\n- src/Foo.swift")
}

@Test func promptBuilderAttachmentsOnly() {
    let body = ComposerPromptBuilder.build(text: "   ", attachmentPaths: ["shot.png"])
    #expect(body == "Attached:\n- shot.png")
}

@Test func displayPathRelativizesInsideWorkspace() {
    let root = "/Users/tim/code/omega"
    #expect(ComposerPromptBuilder.displayPath("/Users/tim/code/omega/app/Foo.swift", workspaceRoot: root) == "app/Foo.swift")
    #expect(ComposerPromptBuilder.displayPath("/tmp/x.png", workspaceRoot: root) == "/tmp/x.png")
}
