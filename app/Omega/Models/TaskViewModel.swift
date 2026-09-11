import Foundation
import OmegaCore

struct ComposerAttachment: Identifiable, Equatable {
    let id: UUID
    let displayPath: String
    let absolutePath: String
    let isImage: Bool

    init(id: UUID = UUID(), displayPath: String, absolutePath: String, isImage: Bool) {
        self.id = id
        self.displayPath = displayPath
        self.absolutePath = absolutePath
        self.isImage = isImage
    }

    static func isImagePath(_ path: String) -> Bool {
        let ext = (path as NSString).pathExtension.lowercased()
        return ["png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "heic", "bmp"].contains(ext)
    }
}

@MainActor
@Observable
final class TaskViewModel {
    let taskId: String
    private let client: OmegaClientProtocol

    let transcript = TranscriptModel()
    var task: OmegaTask?
    var composerText: String = ""
    /// UTF-16 caret offset into `composerText`. Driven by the AppKit text view.
    var composerCursor: Int = 0
    var attachments: [ComposerAttachment] = []
    /// Workspace-relative paths for `@` mentions. Filled asynchronously after `start()`.
    var workspaceFiles: [String] = []
    var mode: TaskMode = .build
    var selectedModel: String = "opus"
    var isSending = false
    var errorMessage: String?
    var statusLine: StatusLine?

    private var streamTask: Task<Void, Never>?
    private var tickTask: Task<Void, Never>?
    private var indexTask: Task<Void, Never>?

    init(taskId: String, client: OmegaClientProtocol) {
        self.taskId = taskId
        self.client = client
    }

    func start() async {
        if let loaded = try? await client.task(id: taskId) {
            task = loaded
            mode = loaded.mode
            selectedModel = loaded.model
        }
        if let history = try? await client.taskHistory(id: taskId) {
            for event in history { transcript.apply(event) }
        }
        subscribeToLiveEvents()
        startStatusTicking()
        refreshWorkspaceIndex()
    }

    func stop() {
        streamTask?.cancel()
        tickTask?.cancel()
        indexTask?.cancel()
    }

    func refreshWorkspaceIndex() {
        let root = task?.workspaceRoot
        guard let root, !root.isEmpty else { return }
        indexTask?.cancel()
        indexTask = Task { [weak self] in
            let files = await Task.detached(priority: .utility) {
                WorkspaceFileIndex.list(root: root)
            }.value
            guard !Task.isCancelled else { return }
            self?.workspaceFiles = files
        }
    }

    func attach(urls: [URL]) {
        let root = task?.workspaceRoot
        for url in urls {
            let absolute = url.standardizedFileURL.path
            if attachments.contains(where: { $0.absolutePath == absolute }) { continue }
            let display = ComposerPromptBuilder.displayPath(absolute, workspaceRoot: root)
            attachments.append(ComposerAttachment(
                displayPath: display,
                absolutePath: absolute,
                isImage: ComposerAttachment.isImagePath(absolute)
            ))
        }
    }

    func removeAttachment(_ id: UUID) {
        attachments.removeAll { $0.id == id }
    }

    func insertMention(_ path: String) {
        guard let query = ComposerMentions.query(in: composerText, cursorUTF16: composerCursor) else {
            composerText += "`\(path)` "
            composerCursor = (composerText as NSString).length
            return
        }
        let ns = composerText as NSString
        let replacement = "`\(path)` "
        composerText = ns.replacingCharacters(in: query.utf16Range, with: replacement)
        composerCursor = query.location + (replacement as NSString).length
    }

    private func subscribeToLiveEvents() {
        streamTask?.cancel()
        streamTask = Task { [weak self, taskId, client] in
            let stream = client.taskEventStream(id: taskId)
            do {
                for try await event in stream {
                    guard let self else { return }
                    self.transcript.apply(event)
                    if case .toolStart = event.payload {} // transcript already reduces this
                }
            } catch {
                self?.errorMessage = error.localizedDescription
            }
        }
    }

    private func startStatusTicking() {
        tickTask?.cancel()
        tickTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.statusLine = self.transcript.statusLine()
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
    }

    func send() async {
        guard !isSending else { return }
        let text = ComposerPromptBuilder.build(
            text: composerText,
            attachmentPaths: attachments.map(\.displayPath)
        )
        guard !text.isEmpty else { return }
        isSending = true
        transcript.addUserMessage(text, mode: mode.rawValue)
        composerText = ""
        composerCursor = 0
        attachments = []
        defer { isSending = false }
        do {
            try await client.sendPrompt(taskId: taskId, text: text, mode: mode)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func cancelRun() async {
        try? await client.cancelTask(taskId: taskId)
    }

    func undo() async {
        try? await client.undo(taskId: taskId)
    }

    func answerAskUser(requestId: String, chosen: [String]) async {
        transcript.resolveAskUser(requestId: requestId, chosen: chosen)
        try? await client.answer(taskId: taskId, requestId: requestId, values: chosen)
    }

    func resolveConfirm(requestId: String, approved: Bool) async {
        transcript.resolveConfirm(requestId: requestId, approved: approved)
        try? await client.confirm(taskId: taskId, requestId: requestId, approved: approved)
    }

    func setModel(_ model: String) async {
        selectedModel = model
        try? await client.setModel(taskId: taskId, model: model)
    }

    func setMode(_ newMode: TaskMode) async {
        mode = newMode
        try? await client.setMode(taskId: taskId, mode: newMode)
    }
}
