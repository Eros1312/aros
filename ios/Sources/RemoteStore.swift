import SwiftUI

struct RemoteCache: Codable {
    let server: String
    let dashboard: Dashboard
    let resources: [Resource]
    let inbox: [RemoteInbox]
    let user: RemoteUser
}

@MainActor extension AppStore {
    func connect(server: String, email: String, password: String, demo: Bool = false) async throws {
        var client = try APIClient.make(url: server)
        let result: LoginResult = try await client.request(demo ? "/auth/demo" : "/auth/login", method: "POST", body: demo ? [:] : ["email":email,"password":password])
        client.token = result.accessToken
        let user: RemoteUser = try await client.request("/me")
        try SessionKeychain.save(token: result.accessToken, server: client.baseURL.absoluteString)
        UserDefaults.standard.set(client.baseURL.absoluteString, forKey: APIClient.serverKey)
        remoteRevision += 1
        clearRemoteData()
        try? FileManager.default.removeItem(at: cacheURL())
        apiClient = client; remoteUser = user
        await refreshRemote()
    }
    func restoreSession() async {
        guard apiClient == nil, let token = SessionKeychain.read(server: APIClient.savedURL), let client = try? APIClient.make(url: APIClient.savedURL, token: token) else { return }
        apiClient = client
        if let data = try? Data(contentsOf: cacheURL()), let cached = try? JSONDecoder().decode(RemoteCache.self, from: data), cached.server == client.baseURL.absoluteString {
            dashboard = cached.dashboard; resources = cached.resources; remoteInbox = cached.inbox; remoteUser = cached.user; state = .stale
        } else { clearRemoteData() }
        await refreshRemote()
    }
    func refreshRemote() async {
        guard let client = apiClient else { return }
        remoteRevision += 1; let revision = remoteRevision
        state = .loading
        do {
            async let home: Dashboard = client.request("/dashboard")
            async let records: [Resource] = client.request("/items?limit=1000")
            async let inbox: [RemoteInbox] = client.request("/inbox")
            async let connections: [RemoteConnector] = client.request("/connectors")
            async let automations: [RemoteAutomation] = client.request("/automations")
            async let runs: [RemoteRun] = client.request("/automations/runs/history")
            async let messages: [RemoteMessage] = client.request("/chat")
            async let user: RemoteUser = client.request("/me")
            let loaded = try await (home, records, inbox, connections, automations, runs, messages, user)
            guard revision == remoteRevision else { return }
            dashboard = loaded.0; resources = loaded.1; remoteInbox = loaded.2; remoteConnectors = loaded.3
            remoteAutomations = loaded.4; remoteRuns = loaded.5; remoteMessages = loaded.6; remoteUser = loaded.7
            if let pending = remoteChatJob, ["pending", "running"].contains(pending.status),
               let latest: RemoteAgentJob = try? await client.request("/agents/runs/\(pending.id)"), revision == remoteRevision { remoteChatJob = latest }
            remoteHealth = nil
            if let health: RemoteHealth = try? await client.request("/server"), revision == remoteRevision {
                remoteHealth = health
                dashboard = Dashboard(city: dashboard.city, temperature: dashboard.temperature, weather: dashboard.weather,
                    summaries: dashboard.summaries, attention: dashboard.attention, metrics: health.metrics, syncedAt: dashboard.syncedAt)
            }
            guard revision == remoteRevision else { return }
            state = .ready; resourceError = nil
            await LocalReminders.synchronize(resources)
            guard revision == remoteRevision else { return }
            try? JSONEncoder().encode(RemoteCache(server: client.baseURL.absoluteString, dashboard: dashboard, resources: resources, inbox: remoteInbox, user: loaded.7)).write(to: cacheURL(), options: cacheOptions)
        } catch {
            guard revision == remoteRevision else { return }
            if let apiError = error as? APIError, apiError.status == 401 {
                await logout(localOnly: true)
                resourceError = "La sesión ha caducado. Vuelve a conectar tu servidor en Ajustes."
            } else { state = .offline; resourceError = error.localizedDescription }
        }
    }
    func logout(localOnly: Bool = false) async {
        let client = apiClient
        await LocalReminders.clearPending()
        remoteRevision += 1
        if let client { SessionKeychain.remove(server: client.baseURL.absoluteString) }
        try? FileManager.default.removeItem(at: cacheURL())
        apiClient = nil; remoteUser = nil; clearRemoteData()
        dashboard = DemoData.dashboard(); state = .ready
        if !localOnly, let client { let _: OKResult? = try? await client.request("/auth/logout", method: "POST", body: [:]) }
    }
    func clearRemoteData() {
        remoteChatEpoch += 1; remoteChatJob = nil; isSending = false; remoteChatRequestKey = nil; remoteChatRequestText = nil
        resources = []; remoteInbox = []; remoteConnectors = []; remoteAutomations = []; remoteRuns = []; remoteMessages = []; remoteHealth = nil
        dashboard = Dashboard(city: "Sin ubicación", temperature: "—", weather: "Sin conectar", summaries: [], attention: [], metrics: [], syncedAt: nil)
    }
    private func cacheURL() -> URL {
        let folder = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Aros")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder.appendingPathComponent("dashboard-cache.json")
    }
    private var cacheOptions: Data.WritingOptions {
        #if os(iOS)
        return [.atomic, .completeFileProtection]
        #else
        return [.atomic]
        #endif
    }
    func performRemote(_ operation: () async throws -> Void) async {
        do { try await operation() }
        catch { resourceError = error.localizedDescription }
    }
    func updateInbox(_ id: String, state: String) async throws {
        guard let client = apiClient else { return }
        var body: [String: Any] = ["state":state]
        if state == "snoozed" { body["snoozed_until"] = ISO8601DateFormatter().string(from: .now.addingTimeInterval(86400)) }
        let _: RemoteInbox = try await client.request("/inbox/\(id)", method: "PATCH", body: body)
        await refreshRemote()
    }
    func saveResource(_ draft: ResourceDraft, id: String? = nil, confirmed: Bool = false) async throws {
        guard let client = apiClient else { return }
        let body = try draft.body()
        var token: String?
        if draft.kind == "event" {
            guard confirmed else { throw APIError(message: "Confirma los datos del evento antes de guardar", status: 409) }
            var payload = body
            if let id { payload["id"] = id }
            token = try await client.confirmation(id == nil ? "event.create" : "event.update", payload: payload)
        }
        let _: Resource = try await client.request(id.map { "/items/\($0)" } ?? "/items", method: id == nil ? "POST" : "PATCH", body: body, confirmation: token)
        await refreshRemote()
    }
    func deleteResource(_ resource: Resource) async throws {
        guard let client = apiClient else { return }
        let token = try await client.confirmation("item.delete", payload: ["id":resource.id])
        let _: OKResult = try await client.request("/items/\(resource.id)", method: "DELETE", confirmation: token)
        await refreshRemote()
    }
    func completeResource(_ resource: Resource) async throws {
        var draft = ResourceDraft(resource: resource); draft.status = resource.status == "done" ? "pending" : "done"
        try await saveResource(draft, id: resource.id)
    }
    @discardableResult
    func sendRemote(_ text: String, itemIDs: [String] = []) async -> ChatResult? {
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, !isSending, let client = apiClient else { return nil }
        if let job = remoteChatJob, ["pending", "running"].contains(job.status) {
            resourceError = "Ya hay un mensaje en curso. Actualiza o cancela su ejecución antes de enviar otro."
            return nil
        }
        resourceError = nil
        remoteChatEpoch += 1
        let epoch = remoteChatEpoch, owner = remoteUser?.id
        func isCurrent() -> Bool { remoteChatEpoch == epoch && remoteUser?.id == owner && apiClient?.token == client.token && apiClient?.baseURL == client.baseURL }
        isSending = true; remoteChatJob = nil
        let selection = Array(Set(itemIDs)).sorted()
        guard selection.count <= 20 else { resourceError = "Selecciona como máximo 20 archivos."; isSending = false; return nil }
        let requestIdentity = prompt + "\u{001F}" + selection.joined(separator: ",")
        let requestKey = remoteChatRequestText == requestIdentity ? remoteChatRequestKey ?? UUID().uuidString : UUID().uuidString
        remoteChatRequestKey = requestKey; remoteChatRequestText = requestIdentity
        defer { if isCurrent() { isSending = false } }
        do {
            var job: RemoteAgentJob = try await client.request("/chat", method: "POST", body: ["text":prompt, "integrated":true, "authorize_internal_write":true, "idempotency_key":requestKey, "item_ids":selection])
            guard isCurrent() else { return nil }
            remoteChatJob = job
            if let history: [RemoteMessage] = try? await client.request("/chat"), isCurrent() { remoteMessages = history }
            let deadline = Date.now.addingTimeInterval(620)
            while ["pending", "running"].contains(job.status) {
                guard isCurrent(), !Task.isCancelled else { return nil }
                guard Date.now < deadline else { throw APIError(message: "La espera ha terminado. El trabajo sigue registrado en Aros; comprueba su estado antes de repetirlo.", status: 0) }
                try await Task.sleep(for: .seconds(1.5))
                guard isCurrent() else { return nil }
                job = try await client.request("/agents/runs/\(job.id)")
                guard isCurrent() else { return nil }
                remoteChatJob = job
            }
            guard isCurrent() else { return nil }
            remoteChatRequestKey = nil; remoteChatRequestText = nil
            guard job.status == "completed" else { throw APIError(message: job.error.isEmpty ? "El trabajo no terminó. Consulta los pasos registrados antes de repetir el mensaje." : job.error, status: 0) }
            let sources = (job.sources ?? []).compactMap { source -> RemoteSource? in
                guard let area = source.area, let kind = source.kind else { return nil }
                return RemoteSource(id: source.id, title: source.title, area: area, kind: kind)
            }
            let result = ChatResult(id: job.execution?.replyMessageId ?? job.id, text: job.reply, sources: sources, mode: "openclaw")
            await refreshRemote()
            guard isCurrent() else { return nil }
            return result
        } catch {
            guard isCurrent() else { return nil }
            resourceError = remoteChatJob == nil ? error.localizedDescription : "\(error.localizedDescription) El mensaje está registrado; no lo vuelvas a enviar sin comprobar su estado."
            return nil
        }
    }
    func cancelRemoteChat() async {
        guard let client = apiClient, let job = remoteChatJob, ["pending", "running"].contains(job.status) else { return }
        let epoch = remoteChatEpoch, owner = remoteUser?.id
        do {
            let cancelled: RemoteAgentJob = try await client.request("/agents/runs/\(job.id)/cancel", method: "POST", body: [:])
            guard epoch == remoteChatEpoch, owner == remoteUser?.id, apiClient?.token == client.token else { return }
            remoteChatJob = cancelled; remoteChatEpoch += 1; isSending = false; remoteChatRequestKey = nil; remoteChatRequestText = nil
        } catch {
            guard epoch == remoteChatEpoch, owner == remoteUser?.id else { return }
            resourceError = error.localizedDescription
        }
    }
    @discardableResult
    func importFiles(_ urls: [URL], area: Area, onImported: ((Resource) -> Void)? = nil) async throws -> [Resource] {
        guard let client = apiClient else { return [] }
        let owner = remoteUser?.id
        var imported: [Resource] = []
        do {
            for url in urls {
                guard owner == remoteUser?.id, apiClient?.token == client.token else { throw APIError(message: "La cuenta cambió durante la importación.", status: 0) }
                let resource = try await client.upload(url, area: area)
                guard owner == remoteUser?.id, apiClient?.token == client.token else { throw APIError(message: "La cuenta cambió durante la importación.", status: 0) }
                imported.append(resource); onImported?(resource)
            }
        } catch {
            if owner == remoteUser?.id, apiClient?.token == client.token { await refreshRemote() }
            throw error
        }
        guard owner == remoteUser?.id, apiClient?.token == client.token else { throw APIError(message: "La cuenta cambió durante la importación.", status: 0) }
        await refreshRemote()
        return imported
    }
    func clearConversation() async throws {
        guard let client = apiClient else { messages = []; return }
        let token = try await client.confirmation("chat.clear", payload: [:])
        let _: OKResult = try await client.request("/chat", method: "DELETE", confirmation: token)
        remoteMessages = []
    }
}
