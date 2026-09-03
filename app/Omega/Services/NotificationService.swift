import AppKit
import OmegaCore
import UserNotifications

@MainActor
final class NotificationService: NSObject, UNUserNotificationCenterDelegate {
    var onFocusTask: ((String) -> Void)?
    var isEnabled = true

    private var previousStatus: [String: TaskStatus] = [:]

    override init() {
        super.init()
        UNUserNotificationCenter.current().delegate = self
    }

    func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func observe(tasks: [OmegaTask]) {
        for task in tasks {
            let previous = previousStatus[task.id]
            previousStatus[task.id] = task.status
            guard isEnabled, let previous, previous != task.status else { continue }
            notifyIfNeeded(task: task, transitionedTo: task.status)
        }
    }

    private func notifyIfNeeded(task: OmegaTask, transitionedTo status: TaskStatus) {
        guard !NSApp.isActive else { return }
        let body: String
        switch status {
        case .waitingInput: body = "Needs your input"
        case .done: body = "Finished"
        case .failed: body = "Failed"
        default: return
        }
        let content = UNMutableNotificationContent()
        content.title = task.title
        content.body = body
        content.userInfo = ["taskId": task.id]
        let request = UNNotificationRequest(
            identifier: "\(task.id)-\(status.rawValue)-\(task.updatedAt)",
            content: content, trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let taskId = response.notification.request.content.userInfo["taskId"] as? String
        Task { @MainActor in
            if let taskId { onFocusTask?(taskId) }
        }
        completionHandler()
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}
