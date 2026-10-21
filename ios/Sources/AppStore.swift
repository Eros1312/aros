import SwiftUI
import Observation

@MainActor @Observable
final class AppStore {
    var dashboard = DemoData.dashboard()
    var state: LoadState = .ready
    var selectedTab: MainTab = .today
    var path: [Destination] = []
    var chatPresented = false
    var chatLaunch: ChatLaunch = .write
    var messages: [ChatMessage] = []
    var appLock = AppLock()
    var apiClient: APIClient?
    var remoteUser: RemoteUser?
    var resources: [Resource] = []
    var remoteInbox: [RemoteInbox] = []
    var remoteConnectors: [RemoteConnector] = []
    var remoteAutomations: [RemoteAutomation] = []
    var remoteRuns: [RemoteRun] = []
    var remoteMessages: [RemoteMessage] = []
    var remoteHealth: RemoteHealth?
    var resourceError: String?
    var isSending = false
    var remoteChatJob: RemoteAgentJob?
    var remoteChatEpoch = 0
    var remoteChatRequestKey: String?
    var remoteChatRequestText: String?
    var remoteRevision = 0
    var connected: Bool { apiClient != nil }
    var isDemo: Bool { remoteUser?.demo ?? true }
    var remoteResourceID: String?

    private var loadRevision = 0
    private let provider: any DashboardProviding

    init(provider: any DashboardProviding = DemoDashboardProvider()) { self.provider = provider }

    func refresh() async {
        if connected { await refreshRemote(); return }
        loadRevision += 1
        let revision = loadRevision
        state = .loading
        do {
            let updated = try await provider.load()
            guard revision == loadRevision else { return }
            // Preserve locally resolved demo items when refreshing.
            let resolved = Set(dashboard.attention.filter(\.resolved).map(\.id))
            dashboard = updated
            for index in dashboard.attention.indices {
                dashboard.attention[index].resolved = resolved.contains(dashboard.attention[index].id)
            }
            state = .ready
        } catch {
            guard revision == loadRevision else { return }
            state = .offline
        }
    }

    func simulate(_ newState: LoadState) {
        loadRevision += 1
        state = newState
    }

    func select(_ tab: MainTab) { path = []; selectedTab = tab }
    func open(_ destination: Destination) { path.append(destination) }
    func openChat(_ launch: ChatLaunch = .write) { chatLaunch = launch; chatPresented = true }
    func resolve(_ id: String) {
        if connected {
            Task { await performRemote { try await self.updateInbox(id, state: "resolved") } }
            return
        }
        guard let index = dashboard.attention.firstIndex(where: { $0.id == id }) else { return }
        dashboard.attention[index].resolved.toggle()
    }

    func send(_ text: String, attachments: [String]) {
        if connected {
            guard attachments.isEmpty else { resourceError = "Importa el archivo desde Archivos o desde el botón de adjuntar."; return }
            Task { await sendRemote(text) }
            return
        }
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty || !attachments.isEmpty else { return }
        messages.append(ChatMessage(isUser: true, text: prompt, attachmentNames: attachments))
        guard state != .empty && state != .loading else {
            messages.append(ChatMessage(isUser: false, text: "Todavía no hay datos disponibles. Carga el ejemplo desde Ajustes para explorar el asistente."))
            return
        }
        if !attachments.isEmpty {
            messages.append(ChatMessage(isUser: false, text: "Has seleccionado \(attachments.count) archivo(s). En esta demostración solo se muestran sus nombres: no se leen, suben ni analizan."))
            return
        }
        let key = prompt.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: Locale(identifier: "es_ES"))
        let area: Area? = key.contains("prisma") ? .prisma : key.contains("servidor") ? .server :
            (key.contains("clase") || key.contains("universidad") || key.contains("organica")) ? .university :
            key.contains("pedido") ? .personal : key.contains("3d") ? .printing : nil
        if let area, let summary = dashboard.summary(for: area) {
            let parts = [summary.headline, summary.detail, summary.footnote].compactMap { $0 }
            let rows = summary.rows.map { "\($0.title): \($0.subtitle)" }
            messages.append(ChatMessage(isUser: false, text: "Resumen de ejemplo · \(area.title)\n\n" + (parts + rows).joined(separator: "\n"), sources: [area]))
        } else {
            let tasks = dashboard.unresolved.map { "• \($0.title). \($0.reason)" }.joined(separator: "\n")
            messages.append(ChatMessage(isUser: false,
                text: "Tu día de ejemplo\n\n" + (tasks.isEmpty ? "Todo al día. Has resuelto los asuntos de atención." : tasks) + "\n\nEsta respuesta usa datos de demostración. El asistente aún no está conectado a una IA.",
                sources: [.university, .prisma, .personal]))
        }
    }
}

enum ChatLaunch { case write, dictate, attach }
