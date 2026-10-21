import SwiftUI
import UniformTypeIdentifiers

/// Editorial composition of the owner's existing resources; artwork is local.
struct TechnologyStudioView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var query = ""
    @State private var selectedKind = "all"
    @State private var creating = false
    @State private var importing = false
    private var resources: [Resource] {
        store.resources.filter { $0.area == .technology }
            .sorted { $0.createdAt == $1.createdAt ? $0.id < $1.id : $0.createdAt > $1.createdAt }
    }
    private var records: [Resource] {
        resources.filter {
            (selectedKind == "all" || $0.kind == selectedKind) &&
            (query.isEmpty || ($0.title + " " + $0.description + " " + $0.source).range(of: query, options: [.caseInsensitive, .diacriticInsensitive]) != nil)
        }
    }
    private var featured: Resource? { records.first { $0.kind == "news" } }
    private var remaining: [Resource] { records.filter { $0.id != featured?.id } }
    private var availableKinds: [String] { Array(Set(resources.map(\.kind))).sorted() }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                header
                if store.state == .offline || store.state == .stale {
                    StateNotice(state: store.state) { Task { await store.refreshRemote() } }
                }
                if resources.contains(where: \.isDemo) { DemoLabel() }
                filters
                if let featured {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Para explorar").font(.title2.bold()).foregroundStyle(ArosTheme.ink)
                        resourceCard(featured, featured: true)
                    }
                }
                if records.isEmpty {
                    ContentUnavailableView(query.isEmpty ? "Tu próxima idea empieza aquí" : "No hay coincidencias", systemImage: "sparkles", description: Text(query.isEmpty ? "Añade recursos o conecta una fuente de noticias. Aquí aparecerán tus datos guardados." : "Prueba otro término o cambia el filtro."))
                } else if !remaining.isEmpty {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Tu biblioteca").font(.title2.bold())
                        Spacer()
                        Text("\(remaining.count) en esta selección").font(.caption).foregroundStyle(ArosTheme.secondary)
                    }.foregroundStyle(ArosTheme.ink)
                    LazyVGrid(columns: typeSize.isAccessibilitySize ? [GridItem(.flexible())] : [GridItem(.adaptive(minimum: 260), spacing: 18)], alignment: .leading, spacing: 18) {
                        ForEach(remaining) { resource in resourceCard(resource, featured: false) }
                    }
                }
                Text("Ilustraciones locales de Aros. No son fotografías de los artículos. Las fechas indican cuándo se guardó el recurso, no cuándo se publicó.")
                    .font(.caption).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
            }.frame(maxWidth: 960, alignment: .leading).padding(20).frame(maxWidth: .infinity)
        }.background { ArosBackground() }
            .navigationTitle("IA & Tecnología").inlineTitle()
            .searchable(text: $query, prompt: "Busca noticias, ideas y recursos")
            .refreshable { await store.refreshRemote() }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("Añadir recurso", systemImage: "plus") { creating = true }
                        Button("Importar archivo", systemImage: "paperclip") { importing = true }
                        NavigationLink(value: Destination.connections) { Label("Conexiones", systemImage: "link") }
                        NavigationLink(value: Destination.automations) { Label("Automatizaciones", systemImage: "arrow.triangle.branch") }
                    } label: { Image(systemName: "plus.circle.fill") }.accessibilityLabel("Añadir, importar y conexiones")
                }
            }
            .sheet(isPresented: $creating) { ResourceEditor(area: .technology, initialKind: "project") }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
                switch result {
                case .success(let urls): Task { await store.performRemote { try await store.importFiles(urls, area: .technology) } }
                case .failure: store.resourceError = "No se pudo seleccionar el archivo"
                }
            }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Label("Tu espacio de ideas", systemImage: "sparkles").font(.subheadline.weight(.medium)); Spacer(); ArosOrb(size: 38) }
                .foregroundStyle(ArosTheme.blue)
            Text("Una ventana a\nlo que viene.").font(.largeTitle.bold()).foregroundStyle(ArosTheme.ink).fixedSize(horizontal: false, vertical: true)
            Text("Noticias para entender. Recursos para construir. Ideas que puedes conversar con Aros.")
                .font(.body).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 18) { counts }
                VStack(alignment: .leading, spacing: 8) { counts }
            }.padding(.top, 6)
        }.padding(.vertical, 10)
    }
    @ViewBuilder private var counts: some View {
        Label("\(resources.count) recursos en esta vista", systemImage: "square.stack").font(.caption.weight(.medium))
        Label("\(resources.filter { $0.kind == "news" }.count) noticias", systemImage: "newspaper").font(.caption.weight(.medium))
    }
    private var filters: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("Filtrar por tipo de recurso", selection: $selectedKind) {
                Text("Todos los recursos").tag("all")
                ForEach(availableKinds, id: \.self) { kind in Text(ResourceKind.names[kind] ?? kind).tag(kind) }
            }.pickerStyle(.menu).tint(ArosTheme.ink)
            Text("\(records.count) resultados en esta vista").font(.caption).foregroundStyle(ArosTheme.secondary)
        }
    }
    private func resourceCard(_ resource: Resource, featured: Bool) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            NavigationLink(value: Destination.resource(resource.id)) {
                VStack(alignment: .leading, spacing: 14) {
                    TechnologyArtwork(kind: resource.kind, large: featured)
                    HStack(alignment: .firstTextBaseline) {
                        Text(resource.kindTitle.uppercased()).font(.caption2.bold()).tracking(1).foregroundStyle(ArosTheme.blue)
                        Spacer()
                        if resource.isDemo { Text("Ejemplo").font(.caption).foregroundStyle(ArosTheme.secondary) }
                    }
                    Text(resource.title).font(featured ? .title2.bold() : .headline).foregroundStyle(ArosTheme.ink)
                        .fixedSize(horizontal: false, vertical: true).strikethrough(resource.status == "done")
                    if !resource.description.isEmpty {
                        Text(String(resource.description.prefix(featured ? 360 : 180))).font(.subheadline).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                    Text("\(sourceLabel(resource)) · \(resource.statusTitle)").font(.caption).foregroundStyle(ArosTheme.secondary)
                    Text("Guardado en Aros · " + displayDate(resource.createdAt)).font(.caption2).foregroundStyle(ArosTheme.secondary)
                    if let date = resource.dueAt { Text("Fecha prevista · " + displayDate(date)).font(.caption).foregroundStyle(ArosTheme.secondary) }
                    Label("Abrir detalle", systemImage: "arrow.up.right").font(.subheadline.weight(.medium)).foregroundStyle(ArosTheme.blue)
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain).accessibilityHint("Abre el recurso completo y sus opciones")
            Divider()
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 14) { actions(resource) }
                VStack(alignment: .leading, spacing: 10) { actions(resource) }
            }
        }.padding(18).glass(radius: 28)
    }
    @ViewBuilder private func actions(_ resource: Resource) -> some View {
        Button {
            store.openChat()
            store.send("Ayúdame a entender este recurso de IA y Tecnología: \(resource.title). Consulta su contenido disponible y explica sus límites. ID: \(resource.id)", attachments: [])
        } label: { Label("Preguntar a Aros", systemImage: "sparkles").font(.subheadline.weight(.medium)) }
            .frame(minHeight: 44).disabled(store.isSending)
        if ["task", "reminder"].contains(resource.kind) && resource.editable {
            Button {
                Task { await store.performRemote { try await store.completeResource(resource) } }
            } label: { Label(resource.status == "done" ? "Pendiente" : "Completar", systemImage: "checkmark.circle").font(.subheadline) }.frame(minHeight: 44)
        }
    }
    private func sourceLabel(_ resource: Resource) -> String {
        if let raw = resource.data["url"]?.text, let url = URL(string: raw), ["http", "https"].contains(url.scheme), let host = url.host { return host }
        return ["rss": "Fuente RSS", "manual": "Añadido por ti", "upload": "Archivo importado", "agent-conversation": "Conversación con Aros", "demo": "Demostración"][resource.source] ?? resource.source
    }
}

private struct TechnologyArtwork: View {
    @Environment(\.accessibilityReduceTransparency) private var opaque
    let kind: String
    let large: Bool
    private var accent: Color { kind == "project" ? .green : kind == "news" ? .blue : .orange }
    private var symbol: String { kind == "project" ? "chevron.left.forwardslash.chevron.right" : kind == "news" ? "cpu" : "square.stack.3d.up" }
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 22).fill(LinearGradient(colors: [accent.opacity(0.12), Color.purple.opacity(0.08), .white], startPoint: .topLeading, endPoint: .bottomTrailing))
            Circle().stroke(accent.opacity(0.15), lineWidth: 1).frame(width: large ? 180 : 130).offset(x: 74, y: 28)
            Circle().stroke(accent.opacity(0.13), lineWidth: 1).frame(width: large ? 220 : 170).offset(x: 74, y: 28)
            RoundedRectangle(cornerRadius: 24).fill(opaque ? Color.white : Color.white.opacity(0.78))
                .frame(width: 100, height: 100).rotationEffect(.degrees(-12)).offset(x: -24, y: 4)
                .shadow(color: accent.opacity(0.13), radius: 18, y: 10)
            Image(systemName: symbol).font(.system(size: 43, weight: .light)).foregroundStyle(LinearGradient(colors: [accent, .purple], startPoint: .topLeading, endPoint: .bottomTrailing)).offset(x: -24, y: 4)
            Image(systemName: "sparkle").font(.system(size: 24, weight: .light)).foregroundStyle(Color.orange.opacity(0.8)).offset(x: 70, y: -35)
            Image(systemName: "circle.grid.2x2.fill").font(.system(size: 18)).foregroundStyle(accent.opacity(0.55)).offset(x: 90, y: 34)
        }.frame(height: large ? 220 : 155).clipped().accessibilityHidden(true)
        // Static artwork: Reduce Motion is respected without animated effects.
    }
}
