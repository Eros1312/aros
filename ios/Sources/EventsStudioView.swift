import SwiftUI

/// Owner-scoped originals and everyday work, using existing authenticated APIs.
struct EventsStudioView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var library: RemoteEventsLibrary?
    @State private var loading = false
    @State private var error: String?
    @State private var query = ""
    @State private var category = "all"
    @State private var kind = "all"
    private let categories = ["poster", "banner", "logo", "context", "other"]
    private var session: String { (store.remoteUser?.id ?? "") + "|" + (store.apiClient?.baseURL.absoluteString ?? "") }
    private var assets: [Resource] {
        let imported = store.resources.filter { $0.area == .events && $0.source == "upload" && $0.data["filename"]?.text.isEmpty == false && $0.status != "archived" }
        let catalog = library?.assets ?? store.resources.filter { $0.area == .events && ["events-file", "events-context", "canva", "canva-codex", "canva-request"].contains($0.source) }
        var ids = Set<String>()
        return EventsAssetOrder.ordered((catalog + imported).filter { $0.status != "archived" && ids.insert($0.id).inserted })
    }
    private var work: [Resource] {
        let ids = Set(assets.map(\.id))
        return store.resources.filter { $0.area == .events && $0.status != "archived" && !ids.contains($0.id) }.sorted { $0.updatedAt > $1.updatedAt }
    }
    private var availableKinds: [String] { Array(Set((assets + work).map(\.kind))).sorted() }
    private var filteredAssets: [Resource] { assets.filter(matches) }
    private var filteredWork: [Resource] { work.filter(matches) }
    private var pendingDesign: Bool { assets.contains { $0.source == "canva-request" && ["pending", "active"].contains($0.status) } }
    private var columns: [GridItem] { typeSize.isAccessibilitySize ? [GridItem(.flexible())] : [GridItem(.adaptive(minimum: 270), spacing: 18)] }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                header
                if store.state == .offline || store.state == .stale { StateNotice(state: store.state) { Task { await store.refreshRemote(); await load() } } }
                if loading { ProgressView("Consultando tu biblioteca…") }
                if let error { VStack(alignment: .leading, spacing: 8) { Text(error).font(.subheadline).foregroundStyle(.red); Button("Volver a intentar") { Task { await load() } }.frame(minHeight: 44) }.padding(16).glass() }
                filters
                if filteredAssets.isEmpty && filteredWork.isEmpty && !loading {
                    ContentUnavailableView(query.isEmpty ? "Un espacio para tus próximos proyectos" : "No hay coincidencias", systemImage: "square.stack", description: Text(query.isEmpty ? "Tus originales, diseños y tareas aparecerán aquí. Usa Canva y gestión para consultar las opciones disponibles." : "Prueba otra búsqueda, categoría o tipo de recurso."))
                }
                ForEach(categories, id: \.self) { group in
                    let items = filteredAssets.filter { grouping($0) == group }
                    if !items.isEmpty {
                        VStack(alignment: .leading, spacing: 14) {
                            sectionTitle(categoryTitle(group), count: items.count)
                            LazyVGrid(columns: columns, alignment: .leading, spacing: 18) {
                                ForEach(items) { asset in assetCard(asset) }
                            }
                        }
                    }
                }
                if !filteredWork.isEmpty {
                    VStack(alignment: .leading, spacing: 14) {
                        sectionTitle("Agenda y trabajo", count: filteredWork.count)
                        LazyVGrid(columns: columns, alignment: .leading, spacing: 18) { ForEach(filteredWork) { resource in workCard(resource) } }
                    }
                }
                NavigationLink { ResourceListView(area: .events) } label: { Label("Todos los recursos · añadir o importar", systemImage: "folder.badge.plus").frame(minHeight: 44) }
                Text("Las vistas previas proceden de tus materiales autorizados. El nombre o la categoría no acreditan una aprobación. Los originales se conservan.")
                    .font(.caption).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
            }.frame(maxWidth: 1000, alignment: .leading).padding(20).frame(maxWidth: .infinity)
        }.background { ArosBackground() }.navigationTitle("Eventos").inlineTitle()
            .toolbar { ToolbarItem(placement: .primaryAction) { NavigationLink { EventsLibraryView() } label: { Label("Canva y gestión", systemImage: "slider.horizontal.3") }.accessibilityLabel("Canva, autorización y preparación de diseños") } }
            .task(id: session) { library = nil; error = nil; await load() }
            .refreshable { await store.refreshRemote(); await load() }
            .task(id: pendingDesign) {
                guard pendingDesign else { return }
                while !Task.isCancelled && pendingDesign {
                    do { try await Task.sleep(for: .seconds(3)) } catch { return }
                    await load()
                }
            }
    }
    private var header: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack { Text("Eventos").font(.caption.weight(.bold)).tracking(3).foregroundStyle(Area.events.tint); Spacer(); Image(systemName: "square.stack.3d.up").font(.title2).foregroundStyle(Area.events.tint).accessibilityHidden(true) }
            Text("Todo lo que\nda forma a tu idea.").font(.largeTitle.bold()).foregroundStyle(ArosTheme.ink).fixedSize(horizontal: false, vertical: true)
            Text("Programas, documentos y diseños para tus eventos. Una biblioteca visual junto a tu agenda y tus proyectos.")
                .font(.body).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 16) { counts }
                VStack(alignment: .leading, spacing: 8) { counts }
            }
            if let canva = library?.canva {
                Label(canva.connected ? "Canva autorizado" : "Canva pendiente de autorización", systemImage: canva.connected ? "checkmark.circle" : "link")
                    .font(.caption).foregroundStyle(ArosTheme.secondary)
            }
            NavigationLink { EventsLibraryView() } label: { Label("Canva y gestión", systemImage: "arrow.up.right").font(.subheadline.weight(.medium)).frame(minHeight: 44) }
        }.padding(22).background(LinearGradient(colors: [Color.pink.opacity(0.09), Color.orange.opacity(0.035), .white], startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: 30))
    }
    @ViewBuilder private var counts: some View {
        Label("\(assets.count) materiales en esta vista", systemImage: "photo.stack").font(.caption.weight(.medium))
        Label("\(work.count) recursos de trabajo", systemImage: "checklist").font(.caption.weight(.medium))
    }
    private var filters: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField("Buscar materiales, textos y tareas", text: $query).textFieldStyle(.roundedBorder).accessibilityLabel("Buscar en Eventos")
            ViewThatFits(in: .horizontal) {
                HStack { filterPickers }
                VStack(alignment: .leading) { filterPickers }
            }
            Text("\(filteredAssets.count + filteredWork.count) resultados en esta vista").font(.caption).foregroundStyle(ArosTheme.secondary)
        }
    }
    @ViewBuilder private var filterPickers: some View {
        Picker("Categoría", selection: $category) { Text("Todas las categorías").tag("all"); ForEach(categories, id: \.self) { Text(categoryTitle($0)).tag($0) } }.pickerStyle(.menu)
        Picker("Tipo de recurso", selection: $kind) { Text("Todos los tipos").tag("all"); ForEach(availableKinds, id: \.self) { Text(ResourceKind.names[$0] ?? $0).tag($0) } }.pickerStyle(.menu)
    }
    private func assetCard(_ asset: Resource) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            NavigationLink(value: Destination.resource(asset.id)) {
                VStack(alignment: .leading, spacing: 14) {
                    if hasPreview(asset) {
                        EventsStudioPreview(id: asset.id).id(session + "|" + asset.id).frame(height: 230).frame(maxWidth: .infinity)
                            .padding(12).background(Color.white, in: RoundedRectangle(cornerRadius: 20))
                    } else {
                        Label(grouping(asset) == "context" ? "Referencia de conversación" : "Vista previa no disponible", systemImage: grouping(asset) == "context" ? "text.book.closed" : "doc")
                            .font(.subheadline).foregroundStyle(ArosTheme.secondary).frame(maxWidth: .infinity, minHeight: 130).background(Color.white, in: RoundedRectangle(cornerRadius: 20))
                    }
                    cardText(asset)
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain).accessibilityHint("Abre el detalle y el texto completo del material")
            if asset.source == "upload" { Label("Archivo importado", systemImage: "paperclip").font(.caption).foregroundStyle(ArosTheme.secondary) }
            if asset.source != "upload" && asset.data["review_state"]?.text == "needs_review" { Label("Pendiente de revisar", systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(Color(red: 0.5, green: 0.3, blue: 0.08)) }
            if asset.source != "upload" && asset.data["review_state"]?.text == "approved" { Label("Aprobación registrada", systemImage: "checkmark.seal").font(.caption).foregroundStyle(.green) }
            if asset.isDemo { Text("Ejemplo de demostración").font(.caption).foregroundStyle(ArosTheme.secondary) }
            if let issue = asset.data["error"]?.text, !issue.isEmpty { Text(issue).font(.caption).foregroundStyle(.red) }
            if let state = asset.data["state"]?.text, !state.isEmpty { Text("Estado del diseño: " + state).font(.caption).foregroundStyle(ArosTheme.secondary) }
            if let url = canvaURL(asset) { Link(destination: url) { Label("Abrir en Canva", systemImage: "arrow.up.right.square").frame(minHeight: 44) } }
            if asset.data["filename"]?.text.isEmpty == false {
                if asset.source == "upload" { EventsImportedActions(asset: asset).buttonStyle(.bordered).controlSize(.large) }
                else { EventsOriginalActions(asset: asset).buttonStyle(.bordered).controlSize(.large) }
            }
            askButton(asset)
        }.padding(18).glass(radius: 27)
    }
    private func workCard(_ resource: Resource) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            NavigationLink(value: Destination.resource(resource.id)) {
                VStack(alignment: .leading, spacing: 12) { AreaIcon(area: .events, size: 42); cardText(resource) }.frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain)
            if ["task", "reminder"].contains(resource.kind) && resource.editable {
                Button(resource.status == "done" ? "Marcar pendiente" : "Completar tarea") { Task { await store.performRemote { try await store.completeResource(resource) } } }.buttonStyle(.bordered).frame(minHeight: 44)
            }
            askButton(resource)
        }.padding(18).glass(radius: 27)
    }
    private func cardText(_ resource: Resource) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(resource.kindTitle).font(.caption.weight(.semibold)).foregroundStyle(Area.events.tint)
            Text(resource.title).font(.headline).foregroundStyle(ArosTheme.ink).fixedSize(horizontal: false, vertical: true).strikethrough(resource.status == "done")
            if !resource.description.isEmpty { Text(String(resource.description.prefix(180))).font(.subheadline).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true) }
            Text(resource.statusTitle).font(.caption).foregroundStyle(ArosTheme.secondary)
            if let due = resource.dueAt { Text(displayDate(due)).font(.caption).foregroundStyle(ArosTheme.secondary) }
            Label("Abrir detalle", systemImage: "arrow.up.right").font(.subheadline).foregroundStyle(ArosTheme.blue)
        }
    }
    private func askButton(_ resource: Resource) -> some View {
        Button {
            store.openChat(); store.send("Ayúdame con este recurso de Eventos: \(resource.title). Consulta el contenido disponible; conserva el original y no deduzcas ninguna aprobación. ID: \(resource.id)", attachments: [])
        } label: { Label("Preguntar a Aros", systemImage: "sparkles").font(.subheadline.weight(.medium)).frame(minHeight: 44) }.disabled(store.isSending)
    }
    private func sectionTitle(_ title: String, count: Int) -> some View {
        VStack(alignment: .leading, spacing: 5) { Text(title).font(.title2.bold()).foregroundStyle(ArosTheme.ink); Text("\(count) en esta selección").font(.caption).foregroundStyle(ArosTheme.secondary) }
    }
    private func grouping(_ resource: Resource) -> String { let value = resource.data["category"]?.text ?? ""; return categories.contains(value) ? value : "other" }
    private func categoryTitle(_ category: String) -> String { ["poster": "Carteles", "banner": "Banners", "logo": "Logos", "context": "Referencias", "other": "Otros materiales"][category] ?? category }
    private func matches(_ resource: Resource) -> Bool {
        (category == "all" || category == grouping(resource)) && (kind == "all" || kind == resource.kind) && (query.isEmpty || (resource.title + " " + resource.description + " " + (resource.data["filename"]?.text ?? "")).range(of: query, options: [.caseInsensitive, .diacriticInsensitive]) != nil)
    }
    private func hasPreview(_ resource: Resource) -> Bool {
        let ext = (resource.data["filename"]?.text ?? "").split(separator: ".").last.map(String.init)?.lowercased() ?? ""
        return resource.data["preview_item_id"]?.text.isEmpty == false || ["pdf", "png", "jpg", "jpeg"].contains(ext)
    }
    private func canvaURL(_ resource: Resource) -> URL? {
        guard let raw = resource.data["canva_edit_url"]?.text, let url = URL(string: raw), url.scheme == "https", ["canva.com", "www.canva.com"].contains(url.host), url.user == nil, url.password == nil else { return nil }; return url
    }
    private func load() async {
        guard let client = store.apiClient else { return }
        let expected = session; loading = true
        do {
            let result: RemoteEventsLibrary = try await client.request("/events/library")
            guard session == expected && store.apiClient?.token == client.token else { return }
            library = result; error = nil
        } catch { guard session == expected else { return }; self.error = error.localizedDescription }
        if session == expected { loading = false }
    }
}

/// Same private preview endpoint, fenced against account changes during loading.
private struct EventsStudioPreview: View {
    @Environment(AppStore.self) private var store
    let id: String
    @State private var content: Data?
    @State private var loading = true
    var body: some View {
        Group {
            #if os(iOS)
            if let content, let image = UIImage(data: content) { Image(uiImage: image).resizable().scaledToFit() }
            else { placeholder }
            #else
            if let content, let image = NSImage(data: content) { Image(nsImage: image).resizable().scaledToFit() }
            else { placeholder }
            #endif
        }.accessibilityLabel("Vista previa del material")
            .task(id: id) {
                content = nil; loading = true
                guard let client = store.apiClient else { loading = false; return }
                let owner = store.remoteUser?.id
                var request = URLRequest(url: client.baseURL.appendingPathComponent("api/v1/events/assets/\(id)/preview"))
                request.cachePolicy = .reloadIgnoringLocalCacheData
                request.setValue("Bearer \(client.token)", forHTTPHeaderField: "Authorization")
                let result = try? await URLSession.shared.data(for: request)
                guard !Task.isCancelled, store.remoteUser?.id == owner, store.apiClient?.token == client.token, store.apiClient?.baseURL == client.baseURL else { return }
                if let (data, response) = result, let response = response as? HTTPURLResponse, response.statusCode == 200 { content = data }
                loading = false
            }
    }
    private var placeholder: some View {
        Group {
            if loading { ProgressView("Cargando vista previa…") }
            else { Label("Vista previa no disponible", systemImage: "photo").font(.caption).foregroundStyle(ArosTheme.secondary) }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct EventsImportedActions: View {
    @Environment(AppStore.self) private var store
    let asset: Resource
    @State private var downloaded: URL?
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(busy ? "Descargando…" : "Descargar archivo importado") {
                Task {
                    guard let client = store.apiClient else { return }
                    let owner = store.remoteUser?.id; busy = true; error = nil
                    do {
                        let url = try await client.download(asset)
                        guard !Task.isCancelled, store.remoteUser?.id == owner, store.apiClient?.token == client.token, store.apiClient?.baseURL == client.baseURL else { return }
                        downloaded = url
                    } catch { if store.remoteUser?.id == owner { self.error = error.localizedDescription } }
                    busy = false
                }
            }.disabled(busy).frame(minHeight: 44)
            if let downloaded { ShareLink(item: downloaded) { Label("Abrir o compartir archivo", systemImage: "square.and.arrow.up").frame(minHeight: 44) } }
            if let error { Text(error).font(.caption).foregroundStyle(.red) }
        }
    }
}
