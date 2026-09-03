import Foundation

/// Owns one `/ws/terminals/{id}` connection: binary frames are raw PTY bytes,
/// `sendResize` emits the text control frame `{"resize":[cols,rows]}`.
public final class TerminalSocket: @unchecked Sendable {
    private let task: URLSessionWebSocketTask
    private let incoming: @Sendable (TerminalFrame) -> Void
    private let lock = NSLock()
    private var closed = false

    init(task: URLSessionWebSocketTask, incoming: @escaping @Sendable (TerminalFrame) -> Void) {
        self.task = task
        self.incoming = incoming
        task.resume()
        receiveLoop()
    }

    private func receiveLoop() {
        task.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                switch message {
                case .data(let data):
                    self.incoming(.data(data))
                case .string(let text):
                    if let resize = Self.parseResize(text) {
                        self.incoming(resize)
                    }
                @unknown default:
                    break
                }
                self.receiveLoop()
            case .failure:
                self.markClosed()
            }
        }
    }

    private static func parseResize(_ text: String) -> TerminalFrame? {
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let pair = obj["resize"] as? [Int], pair.count == 2 else { return nil }
        return .resize(cols: pair[0], rows: pair[1])
    }

    public func send(_ data: Data) {
        task.send(.data(data)) { _ in }
    }

    public func sendResize(cols: Int, rows: Int) {
        let payload = ["resize": [cols, rows]]
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else { return }
        task.send(.string(text)) { _ in }
    }

    public func close() {
        markClosed()
    }

    private func markClosed() {
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { return }
        closed = true
        task.cancel(with: .normalClosure, reason: nil)
    }
}
