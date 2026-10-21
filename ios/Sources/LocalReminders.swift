import Foundation
import UserNotifications

// Opt-in local reminders; this is not a substitute for server push notifications.
@MainActor enum LocalReminders {
    private static var generation = 0
    static let preferenceKey = "aros.localReminders"
    static var enabled: Bool { UserDefaults.standard.bool(forKey: preferenceKey) }
    static func setEnabled(_ value: Bool, resources: [Resource]) async throws {
        if value {
            let allowed = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert,.badge,.sound])
            guard allowed else { throw APIError(message: "Activa las notificaciones de Aros en Ajustes del iPhone para recibir recordatorios.", status: 0) }
        }
        UserDefaults.standard.set(value, forKey: preferenceKey)
        await synchronize(resources)
    }
    static func synchronize(_ resources: [Resource]) async {
        generation += 1; let revision = generation
        let center = UNUserNotificationCenter.current()
        let existing = await center.pendingNotificationRequests()
        guard revision == generation else { return }
        center.removePendingNotificationRequests(withIdentifiers: existing.filter { $0.identifier.hasPrefix("aros.resource.") }.map(\.identifier))
        guard enabled else { return }
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime,.withFractionalSeconds]
        var candidates: [(Resource,Date)] = []
        for resource in resources where ["task","reminder","event"].contains(resource.kind) && !["done","archived","cancelled"].contains(resource.status) {
            if resource.data["all_day"] == .bool(true) { continue }
            guard let raw = resource.dueAt else { continue }
            let fallback = ISO8601DateFormatter()
            guard let due = formatter.date(from: raw) ?? fallback.date(from: raw) else { continue }
            let notificationDate = resource.kind == "event" ? due.addingTimeInterval(-900) : due
            if notificationDate.timeIntervalSinceNow > 1 { candidates.append((resource,notificationDate)) }
        }
        for (resource,date) in candidates.sorted(by: { $0.1 < $1.1 }).prefix(60) {
            guard revision == generation else { return }
            let content = UNMutableNotificationContent()
            content.title = resource.title; content.body = resource.area.title + (resource.kind == "event" ? " · Empieza en 15 minutos" : " · Recordatorio de Aros")
            content.sound = .default
            content.userInfo = ["resource_id":resource.id]
            let trigger = UNTimeIntervalNotificationTrigger(timeInterval: max(1,date.timeIntervalSinceNow), repeats: false)
            let id = "aros.resource.\(revision)." + resource.id
            try? await center.add(UNNotificationRequest(identifier: id, content: content, trigger: trigger))
            if revision != generation { center.removePendingNotificationRequests(withIdentifiers: [id]); return }
        }
    }
    static func clearPending() async {
        generation += 1
        let center = UNUserNotificationCenter.current()
        let existing = await center.pendingNotificationRequests()
        center.removePendingNotificationRequests(withIdentifiers: existing.filter { $0.identifier.hasPrefix("aros.resource.") }.map(\.identifier))
    }
}
