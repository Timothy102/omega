import AppKit
import OmegaCore
import SwiftUI

/// AppKit-backed composer: real caret, click-to-place, current-line highlight,
/// Cmd-Return to send, file/image drop + paste. SwiftUI `TextEditor` cannot do this.
struct ComposerTextView: NSViewRepresentable {
    @Binding var text: String
    @Binding var cursorUTF16: Int
    var mentionOpen: Bool
    var onSubmit: () -> Void
    var onFiles: ([URL]) -> Void
    var onMentionKey: (ComposerMentionKey) -> Bool

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = IntrinsicScrollView()
        scroll.drawsBackground = false
        scroll.borderType = .noBorder
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.scrollerStyle = .overlay
        scroll.identifier = NSUserInterfaceItemIdentifier("omega.composer.scroll")

        let textView = ComposerNSTextView()
        textView.delegate = context.coordinator
        context.coordinator.apply(textView)
        scroll.documentView = textView
        context.coordinator.textView = textView
        context.coordinator.scrollView = scroll

        DispatchQueue.main.async {
            textView.window?.makeFirstResponder(textView)
        }
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let textView = context.coordinator.textView else { return }
        context.coordinator.apply(textView)

        if textView.string != text {
            textView.string = text
            let len = (text as NSString).length
            let loc = min(max(cursorUTF16, 0), len)
            textView.setSelectedRange(NSRange(location: loc, length: 0))
        } else if textView.window?.firstResponder !== textView {
            let len = (text as NSString).length
            let loc = min(max(cursorUTF16, 0), len)
            if textView.selectedRange().location != loc {
                textView.setSelectedRange(NSRange(location: loc, length: 0))
            }
        }
        textView.needsDisplay = true
    }

    static func dismantleNSView(_ nsView: NSScrollView, coordinator: Coordinator) {
        coordinator.textView?.delegate = nil
        coordinator.textView = nil
        coordinator.scrollView = nil
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: ComposerTextView
        weak var textView: ComposerNSTextView?
        weak var scrollView: NSScrollView?

        init(parent: ComposerTextView) {
            self.parent = parent
        }

        func apply(_ textView: ComposerNSTextView) {
            textView.onSubmit = { [weak self] in self?.parent.onSubmit() }
            textView.onFiles = { [weak self] urls in self?.parent.onFiles(urls) }
            textView.onMentionKey = { [weak self] key in self?.parent.onMentionKey(key) ?? false }
            textView.mentionOpen = parent.mentionOpen
        }

        func textDidChange(_ notification: Notification) {
            guard let textView else { return }
            parent.text = textView.string
            parent.cursorUTF16 = textView.selectedRange().location
        }

        func textViewDidChangeSelection(_ notification: Notification) {
            guard let textView else { return }
            let loc = textView.selectedRange().location
            if parent.cursorUTF16 != loc {
                parent.cursorUTF16 = loc
            }
            textView.needsDisplay = true
        }
    }
}

enum ComposerMentionKey {
    case up, down, accept, cancel
}

/// Scroll view that reports a sensible intrinsic height so SwiftUI can size the composer.
private final class IntrinsicScrollView: NSScrollView {
    override var intrinsicContentSize: NSSize {
        NSSize(width: NSView.noIntrinsicMetric, height: NSView.noIntrinsicMetric)
    }
}

final class ComposerNSTextView: NSTextView {
    var onSubmit: (() -> Void)?
    var onFiles: (([URL]) -> Void)?
    var onMentionKey: ((ComposerMentionKey) -> Bool)?
    var mentionOpen = false

    override init(frame frameRect: NSRect, textContainer container: NSTextContainer?) {
        super.init(frame: frameRect, textContainer: container)
        configure()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        configure()
    }

    convenience init() {
        let storage = NSTextStorage()
        let layout = NSLayoutManager()
        storage.addLayoutManager(layout)
        let container = NSTextContainer(size: NSSize(width: 200, height: CGFloat.greatestFiniteMagnitude))
        container.widthTracksTextView = true
        container.lineFragmentPadding = 4
        layout.addTextContainer(container)
        self.init(frame: .zero, textContainer: container)
    }

    private func configure() {
        isRichText = false
        importsGraphics = false
        allowsUndo = true
        isAutomaticQuoteSubstitutionEnabled = false
        isAutomaticDashSubstitutionEnabled = false
        isAutomaticTextReplacementEnabled = false
        isAutomaticSpellingCorrectionEnabled = false
        isContinuousSpellCheckingEnabled = false
        usesFontPanel = false
        usesFindBar = true
        isGrammarCheckingEnabled = false
        smartInsertDeleteEnabled = false
        isVerticallyResizable = true
        isHorizontallyResizable = false
        textContainerInset = NSSize(width: 8, height: 8)
        minSize = NSSize(width: 0, height: 56)
        maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        autoresizingMask = [.width]
        drawsBackground = false
        insertionPointColor = .controlAccentColor
        font = .systemFont(ofSize: 14)
        textColor = .labelColor
        selectedTextAttributes = [
            .backgroundColor: NSColor.selectedTextBackgroundColor.withAlphaComponent(0.55),
            .foregroundColor: NSColor.labelColor,
        ]
        registerForDraggedTypes([.fileURL, .png, .tiff, .string])
    }

    func syncContainerWidth() {
        guard let scroll = enclosingScrollView else { return }
        let width = max(scroll.contentSize.width, 40)
        if textContainer?.containerSize.width != width {
            textContainer?.containerSize = NSSize(width: width, height: CGFloat.greatestFiniteMagnitude)
        }
        if abs(frame.size.width - width) > 0.5 {
            frame.size.width = width
        }
    }

    override var acceptsFirstResponder: Bool { true }

    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(self)
        super.mouseDown(with: event)
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        syncContainerWidth()
    }

    override func layout() {
        super.layout()
        syncContainerWidth()
    }

    override func draw(_ dirtyRect: NSRect) {
        drawCurrentLineHighlight()
        super.draw(dirtyRect)
    }

    private func drawCurrentLineHighlight() {
        guard let layoutManager, let textContainer else { return }
        let ns = string as NSString
        let loc = min(selectedRange().location, ns.length)
        let lineRange = ns.lineRange(for: NSRange(location: loc, length: 0))
        let glyphRange = layoutManager.glyphRange(forCharacterRange: lineRange, actualCharacterRange: nil)
        var rect = layoutManager.boundingRect(forGlyphRange: glyphRange, in: textContainer)
        rect.origin.x = 0
        rect.size.width = bounds.width
        rect.origin.y += textContainerOrigin.y
        if rect.height < 16 { rect.size.height = 18 }
        NSColor.labelColor.withAlphaComponent(0.07).setFill()
        rect.fill()
    }

    override func keyDown(with event: NSEvent) {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        let chars = event.charactersIgnoringModifiers ?? ""

        if flags.contains(.command), chars == "\r" || event.keyCode == 36 {
            onSubmit?()
            return
        }

        if mentionOpen, let onMentionKey {
            switch event.keyCode {
            case 126: if onMentionKey(.up) { return }
            case 125: if onMentionKey(.down) { return }
            case 36, 48: if onMentionKey(.accept) { return }
            case 53: if onMentionKey(.cancel) { return }
            default: break
            }
        }

        super.keyDown(with: event)
    }

    override func paste(_ sender: Any?) {
        let board = NSPasteboard.general
        if let urls = Self.readFileURLs(from: board), !urls.isEmpty {
            onFiles?(urls)
            return
        }
        if let image = NSImage(pasteboard: board), let url = Self.writePastedImage(image) {
            onFiles?([url])
            return
        }
        super.paste(sender)
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        canAccept(sender) ? .copy : []
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        canAccept(sender) ? .copy : []
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let board = sender.draggingPasteboard
        if let urls = Self.readFileURLs(from: board), !urls.isEmpty {
            onFiles?(urls)
            return true
        }
        if let image = NSImage(pasteboard: board), let url = Self.writePastedImage(image) {
            onFiles?([url])
            return true
        }
        return false
    }

    private func canAccept(_ sender: NSDraggingInfo) -> Bool {
        let board = sender.draggingPasteboard
        if Self.readFileURLs(from: board)?.isEmpty == false { return true }
        return board.availableType(from: [.png, .tiff]) != nil
    }

    static func readFileURLs(from board: NSPasteboard) -> [URL]? {
        let urls = board.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL]
        let files = urls?.filter(\.isFileURL) ?? []
        return files.isEmpty ? nil : files
    }

    static func writePastedImage(_ image: NSImage) -> URL? {
        guard let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:])
        else { return nil }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("omega-composer", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let url = dir.appendingPathComponent("paste-\(UUID().uuidString.prefix(8)).png")
            try png.write(to: url)
            return url
        } catch {
            return nil
        }
    }
}
