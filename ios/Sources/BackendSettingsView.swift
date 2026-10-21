import SwiftUI

struct BackendSettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var server = APIClient.savedURL
    @State private var email = ""
    @State private var password = ""
    @State private var busy = false
    @State private var error: String?
    @State private var confirmLogout = false
    @State private var confirmClear = false
    @State private var remindersEnabled = LocalReminders.enabled
    var body: some View {
        Form {
            Section {
                HStack(spacing: 14) { ArosOrb(size: 48); VStack(alignment: .leading) { Text("Aros.").font(.title2.bold()); Text("iPhone y Mac, en un mismo espacio").font(.caption).foregroundStyle(ArosTheme.secondary) } }
                DemoLabel()
            }
            if store.connected {
                Section("Tu servidor") {
                    Text(store.apiClient?.baseURL.absoluteString ?? "")
                    Text(store.remoteUser?.email ?? "")
                    Button("Actualizar datos") { Task { await store.refreshRemote() } }
                    Button("Cerrar sesión", role: .destructive) { confirmLogout = true }
                }
                Section("Tu espacio") {
                    NavigationLink(value: Destination.workspace) { Label("Tareas, notas y proyectos", systemImage: "square.grid.2x2") }
                    NavigationLink(value: Destination.calendar) { Label("Calendario", systemImage: "calendar") }
                    NavigationLink(value: Destination.files) { Label("Archivos", systemImage: "folder") }
                    NavigationLink(value: Destination.connections) { Label("Conexiones", systemImage: "link") }
                    NavigationLink(value: Destination.automations) { Label("Automatizaciones", systemImage: "arrow.triangle.branch") }
                }
                Section("Privacidad") {
                    NavigationLink("Agentes · OpenClaw") { RemoteAgentsView() }
                    NavigationLink("Modelo de todo Aros") { ModelSettingsView() }
                    Toggle("Permitir IA externa", isOn: Binding(get: { store.remoteUser?.aiConsent ?? false }, set: { enabled in
                        Task { await store.performRemote {
                            if let client = store.apiClient {
                                let _: [String: Bool] = try await client.request("/me", method: "PATCH", body: ["ai_consent":enabled])
                                await store.refreshRemote()
                            }
                        } }
                    }))
                    Text("Al activar esta opción, tu pregunta, el historial reciente y los recursos autorizados se enviarán al proveedor configurado para el modelo elegido. El operador de tu servidor configura las conexiones. Los resúmenes locales no envían datos a una IA.").font(.caption)
                    Button("Borrar conversación", role: .destructive) { confirmClear = true }
                    Text("La sesión se guarda en Keychain. La copia local de tus datos tiene protección de archivo y se borra al cerrar sesión. La exportación completa está disponible en Ajustes de la web para Mac.").font(.caption)
                }
            } else {
                Section("Conectar tu servidor") {
                    TextField("https://tu-servidor", text: $server).accountInput(url: true)
                    TextField("Email", text: $email).accountInput()
                    SecureField("Contraseña", text: $password)
                    if let error { Text(error).font(.caption).foregroundStyle(.red) }
                    Button(busy ? "Conectando…" : "Conectar") { Task { await connect() } }.disabled(busy || server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || email.isEmpty || password.isEmpty)
                    if server.contains("localhost") || server.contains("127.0.0.1") {
                        Button("Conectar a la demostración local") { Task { await connect(demo: true) } }.disabled(busy)
                    }
                    Text("Introduce la dirección de tu propia instalación. El servidor debe estar publicado con HTTPS. No se envían credenciales a la dirección hasta tocar Conectar. Google se autoriza desde la web de Aros y queda disponible también en iPhone.").font(.caption)
                }
                Section("Explorar la interfaz") {
                    Picker("Estado", selection: Binding(get: { store.state }, set: { store.simulate($0) })) {
                        ForEach(LoadState.allCases) { Text($0.title).tag($0) }
                    }
                    Button("Cargar datos de ejemplo") { Task { await store.refresh() } }
                }
            }
            Section("Recordatorios en este iPhone") {
                Toggle("Avisar de tareas y eventos", isOn: Binding(get: { remindersEnabled }, set: { value in
                    Task {
                        do { try await LocalReminders.setEnabled(value, resources: store.resources); remindersEnabled = value }
                        catch { store.resourceError = error.localizedDescription }
                    }
                }))
                Text("Se programan hasta 60 avisos locales al actualizar los datos. Los cambios del servidor no llegan hasta que abras o actualices la app. Los eventos avisan 15 minutos antes; las tareas, a su hora. Puedes ocultar previsualizaciones en los ajustes de notificaciones de iOS.").font(.caption)
            }
            Section("Acerca de Aros") {
                Text("Cliente experimental de código abierto. Usa tu propia instalación de Aros.").font(.caption)
                Link("Código fuente de Aros", destination: URL(string: "https://github.com/Eros1312/aros")!)
                Link("Licencia AGPL-3.0-only", destination: URL(string: "https://github.com/Eros1312/aros/blob/main/LICENSE")!)
            }
            Section("Acceso a la app") {
                Toggle("Face ID o código del dispositivo", isOn: Binding(get: { store.appLock.enabled }, set: { value in
                    Task {
                        do { try await store.appLock.setEnabled(value) }
                        catch { store.resourceError = error.localizedDescription }
                    }
                }))
                Text("Al salir de la app, deberás autenticarte para volver a abrirla.").font(.caption)
            }
        }.navigationTitle("Ajustes").inlineTitle()
            .confirmationDialog("¿Cerrar sesión y borrar la copia local?", isPresented: $confirmLogout, titleVisibility: .visible) {
                Button("Cerrar sesión", role: .destructive) { Task { await store.logout() } }
            }
            .confirmationDialog("¿Borrar todos los mensajes guardados?", isPresented: $confirmClear, titleVisibility: .visible) {
                Button("Borrar conversación", role: .destructive) { Task { await store.performRemote { try await store.clearConversation() } } }
            }
    }
    private func connect(demo: Bool = false) async {
        busy = true; error = nil; defer { busy = false }
        do { try await store.connect(server: server, email: email, password: password, demo: demo); password = "" }
        catch { self.error = error.localizedDescription }
    }
}

struct ConnectionsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var busy = false
    @State private var disconnecting: RemoteConnector?
    var body: some View {
        List {
            Section { Text("Las autorizaciones de Google se completan en el navegador de la web. Tu cuenta de Aros comparte los datos con iPhone.").font(.caption) }
            ForEach(store.remoteConnectors) { connector in
                Section(connector.name) {
                    LabeledContent("Estado", value: ["connected":"Conectado","disconnected":"Sin conectar","paused":"En pausa","error":"Revisar","syncing":"Sincronizando"][connector.status] ?? connector.status)
                    if let date = connector.lastSync { LabeledContent("Última sincronización", value: displayDate(date)) }
                    if let error = connector.error { Text(error).font(.caption).foregroundStyle(.red) }
                    if connector.implemented {
                        if ["gmail","calendar","drive"].contains(connector.id) {
                            Button("Abrir Aros web para conectar Google") {
                                if let url = store.apiClient?.baseURL { openURL(url.appending(queryItems: [URLQueryItem(name: "section", value: "connections")])) }
                            }
                        }
                        Button("Sincronizar") { run {
                            guard let client = store.apiClient else { return }
                            let _: [String: JSONValue] = try await client.request("/connectors/\(connector.id)/sync", method: "POST", body: [:])
                            await store.refreshRemote()
                        } }.disabled(busy)
                        if connector.status != "disconnected" {
                            Button(connector.status == "paused" ? "Reanudar" : "Pausar") { run {
                                if let client = store.apiClient {
                                    let _: OKResult = try await client.request("/connectors/\(connector.id)", method: "PATCH", body: ["status":connector.status == "paused" ? "connected" : "paused"])
                                    await store.refreshRemote()
                                }
                            } }.disabled(busy)
                            Button("Desconectar", role: .destructive) { disconnecting = connector }
                        }
                    } else { Text("Integración prevista. Todavía no está implementada.").font(.caption).foregroundStyle(ArosTheme.secondary) }
                    if !connector.scopes.isEmpty { DisclosureGroup("Permisos de lectura") { ForEach(connector.scopes, id: \.self) { Text($0).font(.caption) } } }
                }
            }
        }.navigationTitle("Conexiones").inlineTitle().refreshable { await store.refreshRemote() }
            .confirmationDialog("¿Revocar esta conexión? Los recursos importados se conservan.", isPresented: Binding(get: { disconnecting != nil }, set: { if !$0 { disconnecting = nil } }), titleVisibility: .visible) {
                if let connector = disconnecting {
                    Button("Revocar y desconectar", role: .destructive) { run {
                        if let client = store.apiClient {
                            let token = try await client.confirmation("connector.disconnect", payload: ["id":connector.id])
                            let _: OKResult = try await client.request("/connectors/\(connector.id)", method: "DELETE", confirmation: token)
                            await store.refreshRemote()
                        }
                    } }
                }
            }
    }
    private func run(_ action: @escaping () async throws -> Void) {
        busy = true
        Task { await store.performRemote(action); busy = false }
    }
}

struct AutomationsView: View {
    @Environment(AppStore.self) private var store
    @State private var creating = false
    @State private var title = ""
    @State private var kind = "daily_digest"
    @State private var interval = 360
    var body: some View {
        List {
            Section("Tus automatizaciones") {
                ForEach(store.remoteAutomations) { automation in
                    VStack(alignment: .leading, spacing: 9) {
                        Toggle(automation.title, isOn: Binding(get: { automation.enabled }, set: { value in
                            Task { await store.performRemote {
                                if let client = store.apiClient {
                                    let _: OKResult = try await client.request("/automations/\(automation.id)", method: "PATCH", body: ["title":automation.title,"kind":automation.kind,"enabled":value,"interval_minutes":automation.intervalMinutes])
                                    await store.refreshRemote()
                                }
                            } }
                        }))
                        Text("Cada \(automation.intervalMinutes) min · \(displayDate(automation.nextRun))").font(.caption).foregroundStyle(ArosTheme.secondary)
                        Button("Ejecutar ahora") { Task { await store.performRemote {
                            if let client = store.apiClient {
                                let _: [String: String] = try await client.request("/automations/\(automation.id)/run", method: "POST", body: [:])
                                await store.refreshRemote()
                            }
                        } } }.buttonStyle(.bordered)
                    }.padding(.vertical, 5)
                }
            }
            Section("Últimas ejecuciones") {
                ForEach(store.remoteRuns) { run in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(store.remoteAutomations.first(where: { $0.id == run.automationId })?.title ?? "Automatización").font(.subheadline.weight(.medium))
                        Text(run.status + " · " + (run.result.isEmpty ? displayDate(run.createdAt) : run.result)).font(.caption).foregroundStyle(ArosTheme.secondary)
                    }
                }
                if store.remoteRuns.isEmpty { Text("Sin ejecuciones todavía").font(.caption).foregroundStyle(ArosTheme.secondary) }
            }
        }.navigationTitle("Automatizaciones").inlineTitle().refreshable { await store.refreshRemote() }
            .toolbar { ToolbarItem(placement: .primaryAction) { Button("Añadir", systemImage: "plus") { creating = true } } }
            .sheet(isPresented: $creating) {
                NavigationStack {
                    Form {
                        TextField("Nombre", text: $title)
                        Picker("Acción", selection: $kind) {
                            Text("Resumen diario").tag("daily_digest"); Text("Revisar entregas").tag("deadline_review")
                            Text("Sincronizar conexiones").tag("connector_sync"); Text("Revisar servidor").tag("server_health")
                        }
                        Stepper("Cada \(interval) minutos", value: $interval, in: 5...10080, step: 5)
                    }.navigationTitle("Nueva automatización").inlineTitle()
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { creating = false } }
                            ToolbarItem(placement: .confirmationAction) { Button("Guardar") {
                                Task { await store.performRemote {
                                    if let client = store.apiClient {
                                        let _: [String: String] = try await client.request("/automations", method: "POST", body: ["title":title,"kind":kind,"enabled":false,"interval_minutes":interval])
                                        creating = false; title = ""; await store.refreshRemote()
                                    }
                                } }
                            }.disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
                        }
                }
            }
    }
}

@MainActor struct RemoteAgentsView: View {
    @Environment(AppStore.self) private var store
    @State private var status: RemoteAgentStatus?
    @State private var jobs: [RemoteAgentJob] = []
    @State private var agent = "aros"
    @State private var thread = "main"
    private var conversation: [RemoteAgentJob] { jobs.filter { $0.agentId == agent && $0.threadId == thread }.reversed() }
    private var waiting: Bool { conversation.contains { ["pending","running"].contains($0.status) } }
    @State private var prompt = ""
    @State private var error: String?
    @State private var busy = false
    var body: some View {
        List {
            Section("OpenClaw") {
                Text(status?.detail ?? "Comprobando el motor…")
                Text("Modelo: \(status?.model ?? "Comprobando…"). Puedes elegirlo en Ajustes. No se cambia automáticamente a otro modelo.").font(.caption)
            }
            Section("Tu equipo") {
                Picker("Agente", selection: $agent) {
                    ForEach(status?.agents ?? []) { agent in Text(agent.name).tag(agent.id) }
                }
                Picker("Conversación", selection: $thread) {
                    Text("Principal").tag("main")
                    ForEach(Array(Set(jobs.filter { $0.agentId == agent }.map(\.threadId) + [thread])).filter { $0 != "main" }.sorted(), id: \.self) { id in
                        Text(jobs.last(where: { $0.threadId == id && $0.agentId == agent })?.prompt ?? "Nueva conversación").tag(id)
                    }
                }
                Button("Nueva conversación") { thread = UUID().uuidString; prompt = "" }
                TextField("Escribe o continúa…", text: $prompt, axis: .vertical).lineLimit(3...8)
                if store.remoteUser?.aiConsent != true { Text("Activa Permitir IA externa en Ajustes para consultar.").font(.caption) }
                Button(busy ? "Enviando…" : "Enviar mensaje") { Task { await send() } }
                    .disabled(busy || waiting || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || status?.reachable != true || store.remoteUser?.aiConsent != true)
                if let error { Text(error).foregroundStyle(.red).font(.caption) }
            }
            Section("Conversación compartida con Mac") {
                if conversation.isEmpty { Text("Tus consultas aparecerán aquí.").foregroundStyle(.secondary) }
                ForEach(conversation) { job in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(status?.agents.first(where: { $0.id == job.agentId })?.name ?? job.agentId).font(.headline)
                        Text(job.prompt)
                        Text(["pending":"En cola","running":"Trabajando","completed":"Completado","failed":"Error","cancelled":"Cancelado"][job.status] ?? job.status).font(.caption).foregroundStyle(.secondary)
                        if !job.reply.isEmpty { Text(job.reply).textSelection(.enabled); if job.status == "running" { Text("Respuesta en curso…").font(.caption).foregroundStyle(.secondary) } }
                        if let events = job.events, !events.isEmpty { DisclosureGroup("Actividad del agente") { ForEach(Array(events.enumerated()), id: \.offset) { _, event in Text(event.text).font(.caption).padding(.vertical, 3) } } }
                        if let sources = job.contextSources, !sources.isEmpty { DisclosureGroup("Recursos consultados (\(sources.count))") { ForEach(sources) { source in NavigationLink(value: Destination.resource(source.id)) { Text(source.title).font(.caption) } } } }
                        if !job.error.isEmpty { Text(job.error).font(.caption).foregroundStyle(.red) }
                        ForEach(Array(job.actions.enumerated()), id: \.offset) { index, action in
                            VStack(alignment: .leading) {
                                Text(action.type == "prisma.design" ? "Preparar diseño en Canva" : action.type == "item.create" ? "Crear recurso" : action.type == "item.update" ? "Actualizar recurso" : "Completar recurso").font(.headline)
                                Text(action.payload.brief ?? action.payload.title ?? action.payload.id ?? "")
                                if action.type == "prisma.design" { Text("Este diseño usa el modelo de diseño configurado por el operador. Se encola para revisión en Canva.").font(.caption).foregroundStyle(.secondary) }
                                if let detail = action.detail { Text(detail).font(.caption).foregroundStyle(.secondary) }
                                if action.setupRequired == true { NavigationLink(value: Destination.area(.prisma)) { Label("Configurar Canva en PRISMA", systemImage: "link") } }
                                if let description = action.payload.description { Text(description) }
                                Button(action.appliedId == nil ? action.type == "prisma.design" ? "Preparar diseño en Canva" : "Aplicar esta acción" : action.type == "prisma.design" ? "Diseño en cola" : "Aplicado") { Task {
                                    guard let client = store.apiClient else { return }
                                    do { let _: RemoteAppliedAction = try await client.request("/agents/runs/\(job.id)/actions/\(index)", method: "POST", body: [:]); await load() }
                                    catch { self.error = error.localizedDescription }
                                } }.disabled(action.appliedId != nil)
                            }
                        }
                        if ["pending", "running"].contains(job.status) {
                            Button("Cancelar entrega del resultado") { Task {
                                guard let client = store.apiClient else { return }
                                do { let _: RemoteAgentJob = try await client.request("/agents/runs/\(job.id)/cancel", method: "POST", body: [:]); await load() }
                                catch { self.error = error.localizedDescription }
                            } }
                        }
                    }.padding(.vertical, 6)
                }
            }
            Section("OpenClaw · WhatsApp") {
                Text("Vincula tu cuenta desde Aros en el Mac, escaneando el QR con WhatsApp. Después habla con Aros en tu chat contigo mismo.")
                if let baseURL = store.apiClient?.baseURL {
                    Link("Abrir panel de canales", destination: baseURL.appending(queryItems: [URLQueryItem(name: "section", value: "agents")]))
                }
            }
        }.navigationTitle("Agentes").inlineTitle()
            .onChange(of: agent) { _, _ in thread = "main" }
            .refreshable { await load() }
            .task {
                while !Task.isCancelled {
                    await load()
                    do { try await Task.sleep(for: .seconds(jobs.contains { ["pending", "running"].contains($0.status) } ? 1 : 5)) } catch { break }
                }
            }
    }
    private func load() async {
        guard let client = store.apiClient else { return }
        do {
            let active = jobs.filter { ["pending", "running"].contains($0.status) }
            if status == nil || active.isEmpty { status = try await client.request("/agents") }
            if active.isEmpty { jobs = try await client.request("/agents/runs") }
            else {
                for previous in active {
                    let latest: RemoteAgentJob = try await client.request("/agents/runs/\(previous.id)")
                    guard !Task.isCancelled else { return }
                    if let index = jobs.firstIndex(where: { $0.id == latest.id }) { jobs[index] = latest }
                }
            }
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    private func send() async {
        guard let client = store.apiClient else { return }
        busy = true; error = nil; defer { busy = false }
        do {
            let job: RemoteAgentJob = try await client.request("/agents/runs", method: "POST", body: ["agent_id":agent,"thread_id":thread,"text":prompt,"integrated":true,"authorize_internal_write":true,"idempotency_key":UUID().uuidString])
            jobs.insert(job, at: 0); prompt = ""; await load()
        } catch { self.error = error.localizedDescription }
    }
}

@MainActor struct ModelSettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var preferences: RemoteModelPreferences?
    @State private var selected = ""
    @State private var error: String?
    @State private var busy = false
    var body: some View {
        Form {
            Section("Modelo de todo Aros") {
                Text("La elección se aplica al chat y a las nuevas consultas de los agentes. Las opciones dependen de los proveedores que configure el operador de tu servidor. El resumen local funciona sin proveedor de IA.").font(.footnote)
                if let preferences {
                    Picker("Modelo", selection: $selected) {
                        if !preferences.models.contains(where: { $0.id == preferences.model }) { Text(preferences.model + " · No disponible ahora").tag(preferences.model) }
                        ForEach(preferences.models) { model in Text(model.name).tag(model.id) }
                    }
                    if let option = preferences.models.first(where: { $0.id == selected }) { Text(option.detail).font(.caption) }
                    Button(busy ? "Guardando…" : "Usar en todo Aros") { Task { await save() } }.disabled(busy || selected == preferences.model)
                    Text(preferences.freellmDetail).font(.caption)
                    Text(preferences.scope).font(.caption)
                } else if error == nil { ProgressView("Cargando modelos…") }
                if let error { Text(error).foregroundStyle(.red); Button("Actualizar") { Task { await load() } } }
                Text("Si falla el modelo elegido, se muestra el error. No se utiliza otro automáticamente.").font(.caption)
            }
        }.navigationTitle("Modelo de IA").inlineTitle().task { await load() }
    }
    private func load() async {
        guard let client = store.apiClient else { return }
        let revision = store.remoteRevision
        do {
            let result: RemoteModelPreferences = try await client.request("/ai/preferences")
            guard !Task.isCancelled, revision == store.remoteRevision else { return }
            preferences = result; selected = result.model; error = nil
        } catch { if !Task.isCancelled, revision == store.remoteRevision { self.error = error.localizedDescription } }
    }
    private func save() async {
        guard let client = store.apiClient else { return }
        let revision = store.remoteRevision
        busy = true; error = nil
        do {
            let result: RemoteModelPreferences = try await client.request("/ai/preferences", method: "PATCH", body: ["model":selected])
            guard !Task.isCancelled, revision == store.remoteRevision else { busy = false; return }
            preferences = result; selected = result.model; busy = false
        } catch { if revision == store.remoteRevision { self.error = error.localizedDescription }; busy = false }
    }
}
