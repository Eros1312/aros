import SwiftUI

struct AreaDetailView: View {
    @Environment(AppStore.self) private var store
    let area: Area
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 14) {
                    AreaIcon(area: area, size: 52)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(area.title).font(.title2.bold())
                        DemoLabel()
                    }
                }.padding(.top, 8)
                if store.state == .loading {
                    ProgressView("Cargando…").frame(maxWidth: .infinity, minHeight: 150)
                } else if store.state == .empty {
                    ContentUnavailableView("Nada por aquí todavía", systemImage: area.symbol,
                                           description: Text("Los datos aparecerán cuando conectes una fuente."))
                } else if let summary = store.dashboard.summary(for: area) {
                    if store.state == .offline || store.state == .stale {
                        StateNotice(state: store.state) { Task { await store.refresh() } }
                    }
                    if let headline = summary.headline {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(summary.eyebrow ?? "Ahora").font(.caption).foregroundStyle(ArosTheme.secondary)
                            Text(headline).font(.title2.bold())
                            if let detail = summary.detail { Text(detail).font(.headline).foregroundStyle(area.tint) }
                            if let footnote = summary.footnote { Text(footnote).font(.subheadline).foregroundStyle(ArosTheme.secondary) }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(20).glass()
                    }
                    if area == .server {
                        VStack(spacing: 16) {
                            Label("Todos los servicios operativos · ejemplo", systemImage: "checkmark.circle.fill")
                                .font(.subheadline).foregroundStyle(.green)
                            ForEach(Array(store.dashboard.metrics.enumerated()), id: \.element.id) { index, metric in
                                MetricRow(metric: metric, tint: [.cyan, .purple, .orange][index % 3])
                            }
                        }.padding(20).glass()
                    }
                    VStack(alignment: .leading, spacing: 18) {
                        Text(area == .prisma ? "Preparativos" : "Tu resumen").font(.headline)
                        ForEach(summary.rows) { row in SummaryLine(row: row, tint: area.tint) }
                    }.padding(20).glass()
                    let attention = store.dashboard.attention.filter { $0.area == area && !$0.resolved }
                    if !attention.isEmpty {
                        VStack(alignment: .leading, spacing: 16) {
                            Text("Necesita tu atención").font(.headline)
                            ForEach(attention) { item in AttentionRow(item: item) }
                        }.padding(20).glass()
                    }
                    Button { store.openChat(); store.send("Resume \(area.title)", attachments: []) } label: {
                        Label("Pedir un resumen a Aros", systemImage: "sparkles")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }.buttonStyle(.borderedProminent).tint(area.tint)
                    Text("Las integraciones reales se añadirán en una próxima etapa.")
                        .font(.caption).foregroundStyle(ArosTheme.secondary)
                }
            }.padding(20)
        }.navigationTitle(area.title).inlineTitle()
    }
}

struct IdeasView: View {
    @Environment(AppStore.self) private var store
    @State private var area: Area = .technology
    var body: some View {
        VStack(spacing: 0) {
            Picker("Área", selection: $area) {
                Text("IA & Tecnología").tag(Area.technology)
                Text("Impresión 3D").tag(Area.printing)
            }.pickerStyle(.segmented).padding(.horizontal, 20).padding(.top, 12)
            if store.connected { if area == .printing { PrintingDiscoveryView() } else { ResourceListView(area: area) } } else { AreaDetailView(area: area) }
        }
    }
}

struct AttentionRow: View {
    @Environment(AppStore.self) private var store
    let item: AttentionItem
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Button { store.resolve(item.id) } label: {
                Image(systemName: item.resolved ? "checkmark.circle.fill" : "circle")
                    .font(.title2).foregroundStyle(item.resolved ? .green : item.area.tint)
                    .frame(width: 44, height: 44)
            }.buttonStyle(.plain)
                .accessibilityLabel(item.resolved ? "Volver a marcar pendiente: \(item.title)" : "Resolver: \(item.title)")
            NavigationLink(value: Destination.area(item.area)) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(item.title).font(.subheadline.weight(.medium)).foregroundStyle(ArosTheme.ink)
                        .strikethrough(item.resolved)
                    Text(item.reason).font(.caption).foregroundStyle(ArosTheme.secondary)
                    Text(item.area.title).font(.caption2).foregroundStyle(item.area.tint)
                }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            }.buttonStyle(.plain)
        }
    }
}

struct InboxView: View {
    @Environment(AppStore.self) private var store
    @State private var showResolved = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Lo que necesita tu atención.").font(.title2.bold())
                DemoLabel()
                if store.state == .loading { ProgressView("Cargando pendientes…") }
                else if store.state == .empty {
                    ContentUnavailableView("Sin datos todavía", systemImage: "tray", description: Text("Carga la demostración desde Ajustes."))
                } else {
                    if store.state == .offline || store.state == .stale {
                        StateNotice(state: store.state) { Task { await store.refresh() } }
                    }
                    Toggle("Mostrar resueltos", isOn: $showResolved).font(.subheadline)
                    let items = store.dashboard.attention.filter { showResolved || !$0.resolved }
                    if items.isEmpty {
                        ContentUnavailableView("Todo al día", systemImage: "checkmark.circle", description: Text("No tienes asuntos pendientes en esta demostración."))
                    }
                    ForEach(items) { item in AttentionRow(item: item).padding(14).glass() }
                }
            }.padding(20)
        }.navigationTitle("Inbox").inlineTitle()
    }
}

struct GlobalSearchView: View {
    @Environment(AppStore.self) private var store
    @State private var query = ""
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                DemoLabel()
                if store.state == .loading { ProgressView("Cargando búsqueda…") }
                else if store.state == .empty {
                    ContentUnavailableView("Sin datos para buscar", systemImage: "magnifyingglass", description: Text("Carga los datos de ejemplo desde Ajustes."))
                } else {
                    if store.state == .offline || store.state == .stale {
                        StateNotice(state: store.state) { Task { await store.refresh() } }
                    }
                    let results = DashboardSearch.results(in: store.dashboard, query: query)
                    Text(query.isEmpty ? "Explora tus áreas" : "\(results.count) resultados").font(.headline).padding(.vertical, 8)
                    if results.isEmpty { ContentUnavailableView.search(text: query) }
                    ForEach(results) { hit in
                        NavigationLink(value: Destination.area(hit.area)) {
                            HStack(spacing: 12) {
                                AreaIcon(area: hit.area)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(hit.title).font(.subheadline.weight(.medium)).foregroundStyle(ArosTheme.ink)
                                    Text(hit.subtitle).font(.caption).foregroundStyle(ArosTheme.secondary)
                                    Text(hit.area.title).font(.caption2).foregroundStyle(hit.area.tint)
                                }.frame(maxWidth: .infinity, alignment: .leading)
                                Image(systemName: "chevron.right").font(.caption).foregroundStyle(ArosTheme.secondary)
                            }.padding(14).glass()
                        }.buttonStyle(.plain)
                    }
                }
            }.padding(20)
        }.navigationTitle("Buscar").inlineTitle()
            .searchable(text: $query, prompt: "Clases, documentos, pedidos…")
    }
}

struct WeatherView: View {
    @Environment(AppStore.self) private var store
    @State private var details: RemoteWeather?
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        ScrollView {
            VStack(spacing: 18) {
                DemoLabel()
                if store.connected {
                    if let weather = details, let forecast = weather.forecast {
                        Image(systemName: store.dashboard.weatherSymbol).symbolRenderingMode(.multicolor).font(.system(size: 60)).accessibilityHidden(true)
                        Text(weather.location?.label ?? "Tu ciudad").font(.title2)
                        Text("\(Int(forecast.temperature.rounded())) °C").font(.system(size: 56, weight: .light))
                        Text(forecast.description).font(.title3).foregroundStyle(ArosTheme.secondary)
                        VStack(alignment: .leading, spacing: 12) {
                            Label("Sensación \(Int(forecast.feelsLike.rounded())) °C", systemImage: "thermometer.medium")
                            Label("Mínima \(Int(forecast.minimum.rounded()))° · Máxima \(Int(forecast.maximum.rounded()))°", systemImage: "arrow.up.arrow.down")
                            Label("Lluvia \(Int(forecast.rainProbability.rounded()))% · Viento \(Int(forecast.windKmh.rounded())) km/h", systemImage: "cloud.rain")
                            Text(weather.stale ? "Previsión sin actualizar: consulta la última lectura antes de planificar." : forecast.advice).font(.subheadline).foregroundStyle(ArosTheme.secondary)
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(18).glass()
                        if let date = weather.lastSync { Text("Actualizado: \(displayDate(date))").font(.caption).foregroundStyle(ArosTheme.secondary) }
                        Button("Actualizar tiempo") { Task { await load(refresh: true) } }.buttonStyle(.bordered).disabled(busy || weather.status == "paused")
                        if weather.status == "paused" { Text("Conexión en pausa").font(.caption) }
                    } else if !busy { ContentUnavailableView("Sin previsión disponible", systemImage: "cloud", description: Text(details?.configured == false ? "Configura tu ciudad en Conexiones de Aros web." : "Consulta la conexión y vuelve a actualizar.")) }
                    if busy { ProgressView("Consultando el tiempo…") }
                    if let issue = error ?? details?.error { Text(issue).font(.subheadline).foregroundStyle(.red) }
                    Text("Open-Meteo · ciudad configurada, sin GPS. Las condiciones son estimaciones.").font(.caption).foregroundStyle(ArosTheme.secondary)
                } else {
                    Image(systemName: store.dashboard.weatherSymbol).symbolRenderingMode(.multicolor).font(.system(size: 70)).accessibilityHidden(true)
                    Text(store.dashboard.city).font(.title2)
                    Text(store.dashboard.temperature).font(.system(size: 64, weight: .light))
                    Text(store.dashboard.weather).font(.title3).foregroundStyle(ArosTheme.secondary)
                    Text("Previsión ilustrativa de demostración.").font(.subheadline).foregroundStyle(ArosTheme.secondary)
                }
            }.frame(maxWidth: .infinity).padding(24)
        }.navigationTitle("Clima").inlineTitle().task { await load() }.refreshable { await load() }
    }
    private func load(refresh: Bool = false) async {
        guard !busy, let client = store.apiClient else { return }
        busy = true; error = nil; defer { busy = false }
        do {
            if refresh { let _: JSONValue = try await client.request("/connectors/weather/sync", method: "POST", body: [:]) }
            details = try await client.request("/weather")
        } catch { self.error = error.localizedDescription }
    }
}

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var confirmClear = false
    var body: some View {
        @Bindable var store = store
        Form {
            Section {
                HStack(spacing: 14) {
                    ArosOrb(size: 50)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Aros.").font(.title2.bold())
                        Text("Tu espacio personal").font(.subheadline).foregroundStyle(ArosTheme.secondary)
                    }
                }.padding(.vertical, 8)
                DemoLabel()
            }
            Section("Explorar estados de la interfaz") {
                Picker("Estado", selection: Binding(get: { store.state }, set: { store.simulate($0) })) {
                    ForEach(LoadState.allCases) { state in Text(state.title).tag(state) }
                }
                Button("Volver a cargar el ejemplo") { Task { await store.refresh() } }
                Text("Este selector permite comprobar carga, ausencia de datos y problemas de conexión.")
                    .font(.caption).foregroundStyle(ArosTheme.secondary)
            }
            Section("Conexiones") {
                ForEach(["Google", "Aula Virtual", "VPS"], id: \.self) { name in
                    HStack { Text(name); Spacer(); Text("Sin conectar").foregroundStyle(ArosTheme.secondary) }
                }
            }
            Section("Privacidad") {
                Text("Los datos son ejemplos. Las conversaciones y los cambios se guardan solo en memoria y se reinician al cerrar la app. Los archivos adjuntos no se leen ni se suben.")
                    .font(.subheadline)
                Text("El dictado solicita permiso de micrófono y reconocimiento de voz al utilizarlo. Apple puede procesar la voz si el reconocimiento local no está disponible.")
                    .font(.caption).foregroundStyle(ArosTheme.secondary)
                Button("Borrar conversación", role: .destructive) { confirmClear = true }
            }
            Section("Acerca de") {
                Text("Interfaz de iPhone · versión de demostración")
                Text("La app respeta el tamaño de texto, VoiceOver y las preferencias de reducir movimiento y transparencia del sistema.")
                    .font(.caption).foregroundStyle(ArosTheme.secondary)
            }
        }.navigationTitle("Ajustes").inlineTitle()
            .confirmationDialog("¿Borrar la conversación de demostración?", isPresented: $confirmClear, titleVisibility: .visible) {
                Button("Borrar conversación", role: .destructive) { store.messages = [] }
            }
    }
}
