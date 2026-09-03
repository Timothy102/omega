import SwiftUI
import OmegaCore
import SwiftTerm

struct TerminalPaneView: NSViewRepresentable {
    let client: OmegaClientProtocol
    let terminalId: String

    func makeNSView(context: Context) -> TerminalView {
        let view = TerminalView(frame: .zero)
        view.terminalDelegate = context.coordinator
        context.coordinator.attach(to: view, client: client, terminalId: terminalId)
        return view
    }

    func updateNSView(_ nsView: TerminalView, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    static func dismantleNSView(_ nsView: TerminalView, coordinator: Coordinator) {
        coordinator.detach()
    }

    final class Coordinator: NSObject, TerminalViewDelegate, @unchecked Sendable {
        private weak var terminalView: TerminalView?
        private var socket: TerminalSocket?

        func attach(to view: TerminalView, client: OmegaClientProtocol, terminalId: String) {
            guard socket == nil else { return }
            terminalView = view
            socket = client.terminalStream(id: terminalId) { [weak self] frame in
                guard case .data(let payload) = frame else { return }
                Task { @MainActor [weak self] in
                    self?.terminalView?.feed(byteArray: Array(payload)[...])
                }
            }
        }

        func detach() {
            socket?.close()
            socket = nil
        }

        func send(source: TerminalView, data: ArraySlice<UInt8>) {
            socket?.send(Data(data))
        }

        func sizeChanged(source: TerminalView, newCols: Int, newRows: Int) {
            socket?.sendResize(cols: newCols, rows: newRows)
        }

        func setTerminalTitle(source: TerminalView, title: String) {}
        func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
        func scrolled(source: TerminalView, position: Double) {}
        func rangeChanged(source: TerminalView, startY: Int, endY: Int) {}
    }
}
