import Foundation
import OmegaCore

@MainActor
@Observable
final class TaskViewModel {
    let taskId: String
    private let client: OmegaClientProtocol

    let transcript = TranscriptModel()
    var task: OmegaTask?
    var composerText: String = ""
    var mode: TaskMode = .build
    var selectedModel: String = "opus"
    var isSending = false
    var errorMessage: String?
    var statusLine: StatusLine?

    private var streamTask: Task<Void, Never>?
    private var tickTask: Task<Void, Never>?

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
    }

    func stop() {
        streamTask?.cancel()
        tickTask?.cancel()
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
        let text = composerText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }
        transcript.addUserMessage(text, mode: mode.rawValue)
        composerText = ""
        isSending = true
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
