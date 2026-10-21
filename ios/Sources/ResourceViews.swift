import SwiftUI
import UniformTypeIdentifiers

struct ResourceListView: View {
    @Environment(AppStore.self) private var store
    let area: Area?
    var kinds: [String] = []
    var title: String = "Tu espacio"
    @State private var query = ""
    @State private var selectedKind = "all"
    @State private var creating = false
    @State private var importing = false
    private var universitySummary: Bool { area == .university && selectedKind == "all" && query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var universityResources: [Resource] { store.resources.filter { $0.area == .university } }
    private var upcoming: [Resource] { UniversityResourceOrder.upcoming(universityResources) }
    private var records: [Resource] {
        let filtered = store.resources.filter { resource in
            (area == nil || resource.area == area) && (kinds.isEmpty || kinds.contains(resource.kind)) &&
            (selectedKind == "all" || resource.kind == selectedKind) &&
            (!universitySummary || resource.kind != "event") &&
            (query.isEmpty || (resource.title + " " + resource.description + " " + (resource.data["course_title"]?.text ?? "")).range(of: query, options: [.caseInsensitive,.diacriticInsensitive]) != nil)
        }
        return area == .university ? UniversityResourceOrder.ordered(filtered) : filtered
    }
    var body: some View {
        List {
            Section { DemoLabel() }
            if area == .university { Section { UniversityCalendarSourceView() } }
            if area == nil && kinds.isEmpty {
                Section("Organizar") {
                    NavigationLink(value: Destination.calendar) { Label("Calendario", systemImage: "calendar") }
                    NavigationLink(value: Destination.files) { Label("Archivos", systemImage: "folder") }
                    NavigationLink(value: Destination.automations) { Label("Automatizaciones", systemImage: "arrow.triangle.branch") }
                    NavigationLink(value: Destination.connections) { Label("Conexiones", systemImage: "link") }
                }
            }
            Section {
                Picker("Tipo", selection: $selectedKind) {
                    Text(area == .university ? "Resumen y recursos" : "Todo").tag("all")
                    ForEach(ResourceKind.names.keys.sorted(), id: \.self) { kind in Text(ResourceKind.names[kind]!).tag(kind) }
                }
            }
            if universitySummary {
                Section("Ahora y a continuación") {
                    Text("\(universityResources.filter { $0.kind == "event" }.count) clases y exámenes · \(universityResources.filter { $0.kind == "document" }.count) documentos").font(.caption).foregroundStyle(ArosTheme.secondary)
                    NavigationLink(value: Destination.calendar) { Label("Ver calendario", systemImage: "calendar") }
                    ForEach(Array(upcoming.prefix(6))) { event in
                        NavigationLink(value: Destination.resource(event.id)) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(event.title).font(.subheadline.weight(.medium))
                                if let date = event.dueAt { Text(displayDate(date)).font(.caption).foregroundStyle(ArosTheme.secondary) }
                                if let location = event.data["location"]?.text { Text(location).font(.caption2).foregroundStyle(ArosTheme.secondary) }
                            }
                        }
                    }
                    if upcoming.isEmpty { Text("No hay clases ni exámenes próximos en el horario cargado.").font(.caption) }
                    Button { selectedKind = "document" } label: { Label("Ver documentos", systemImage: "doc.text") }
                    Text("Los recursos están debajo. La búsqueda incluye todo el horario, también el historial.").font(.caption).foregroundStyle(ArosTheme.secondary)
                }
            }
            if store.state == .offline || store.state == .stale {
                Section { StateNotice(state: store.state) { Task { await store.refreshRemote() } } }
            }
            if records.isEmpty {
                ContentUnavailableView(query.isEmpty ? "Nada por aquí todavía" : "No hay resultados", systemImage: "tray", description: Text("Añade un recurso o conecta una fuente."))
            }
            ForEach(records) { resource in
                NavigationLink(value: Destination.resource(resource.id)) {
                    HStack(spacing: 12) {
                        AreaIcon(area: resource.area)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(resource.title).font(.subheadline.weight(.medium)).strikethrough(resource.status == "done")
                            Text("\(resource.kindTitle) · \(resource.statusTitle)").font(.caption).foregroundStyle(ArosTheme.secondary)
                            if let course = resource.data["course_title"]?.text { Text(course).font(.caption2).foregroundStyle(ArosTheme.secondary) }
                            if let date = resource.dueAt { Text(displayDate(date)).font(.caption2).foregroundStyle(ArosTheme.secondary) }
                        }
                    }.padding(.vertical, 5)
                }.swipeActions(edge: .leading) {
                    if ["task","reminder"].contains(resource.kind) && resource.editable {
                        Button { Task { await store.performRemote { try await store.completeResource(resource) } } } label: {
                            Label(resource.status == "done" ? "Pendiente" : "Completar", systemImage: "checkmark")
                        }.tint(.green)
                    }
                }
            }
        }.scrollContentBackground(.hidden).background { ArosBackground() }
            .navigationTitle(area?.title ?? title).inlineTitle()
            .searchable(text: $query, prompt: "Busca en tus datos")
            .refreshable { await store.refreshRemote() }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("Añadir recurso", systemImage: "plus") { creating = true }
                        Button("Importar archivo", systemImage: "paperclip") { importing = true }
                    } label: { Image(systemName: "plus.circle.fill") }.accessibilityLabel("Añadir o importar")
                }
            }
            .sheet(isPresented: $creating) {
                ResourceEditor(area: area ?? .personal, initialKind: kinds.count == 1 ? kinds[0] : area == .printing ? "model" : "task")
            }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
                switch result {
                case .success(let urls): Task { await store.performRemote { try await store.importFiles(urls, area: area ?? .personal) } }
                case .failure: store.resourceError = "No se pudo seleccionar el archivo"
                }
            }
    }
}

func displayDate(_ value: String) -> String {
    let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime,.withFractionalSeconds]
    let fractional = format.date(from: value); format.formatOptions = [.withInternetDateTime]
    guard let date = fractional ?? format.date(from: value) else { return value }
    return date.formatted(.dateTime.day().month(.abbreviated).hour().minute().locale(Locale(identifier: "es_ES")))
}

struct ResourceDetailView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let id: String
    @State private var editing = false
    @State private var confirmDelete = false
    @State private var downloaded: URL?
    @State private var downloading = false
    var body: some View {
        if let resource = store.resources.first(where: { $0.id == id }) {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    HStack(spacing: 14) {
                        AreaIcon(area: resource.area, size: 48)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(resource.title).font(.title2.bold())
                            Text(resource.kindTitle + " · " + resource.area.title).font(.caption).foregroundStyle(ArosTheme.secondary)
                        }
                    }
                    if resource.isDemo { DemoLabel() }
                    Text(resource.description.isEmpty ? "Sin descripción" : resource.description)
                        .font(.body).foregroundStyle(ArosTheme.secondary).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(18).glass()
                    LabeledContent("Estado", value: resource.statusTitle)
                    if let date = resource.dueAt { LabeledContent("Fecha", value: displayDate(date)) }
                    ForEach(resource.data.keys.sorted(), id: \.self) { key in
                        if !["url","mime","size","partial","extracted","sha256","source_sha256","source_url","source_label","verified_source","indexed","thread_id"].contains(key), !(key == "exam" && resource.data[key] == .bool(false)), let value = resource.data[key], !value.text.isEmpty {
                            LabeledContent(["end_at":"Fin","location":"Lugar","filename":"Archivo","source_page":"Página del PDF","group":"Grupo","academic_year":"Curso académico","language":"Idioma","timezone":"Zona horaria","exam":"Examen","course_title":"Asignatura"][key] ?? key.replacingOccurrences(of: "_", with: " "), value: key == "end_at" ? displayDate(value.text) : value.text)
                        }
                    }
                    if let raw = resource.data["url"]?.text, let url = URL(string: raw), ["http","https"].contains(url.scheme) {
                        Link(destination: url) { Label("Abrir fuente original", systemImage: "arrow.up.right.square") }.frame(minHeight: 44)
                    }
                    if resource.source == "upload" || (["university", "university-browser"].contains(resource.source) && resource.data["filename"] != nil) {
                        if let downloaded { ShareLink(item: downloaded) { Label("Compartir archivo descargado", systemImage: "square.and.arrow.up") } }
                        else {
                            Button {
                                downloading = true
                                Task {
                                    await store.performRemote { if let client = store.apiClient { downloaded = try await client.download(resource) } }
                                    downloading = false
                                }
                            } label: { Label(downloading ? "Descargando…" : "Descargar archivo", systemImage: "arrow.down.doc") }
                                .disabled(downloading).frame(minHeight: 44)
                        }
                    }
                    if ["task","reminder"].contains(resource.kind) && resource.editable {
                        Button(resource.status == "done" ? "Marcar pendiente" : "Completar tarea") { Task { await store.performRemote { try await store.completeResource(resource) } } }
                            .buttonStyle(.borderedProminent)
                    }
                    Button { store.openChat(); store.send("Resume " + resource.title, attachments: []) } label: {
                        Label("Preguntar a Aros", systemImage: "sparkles")
                    }.buttonStyle(.bordered)
                    Text("Fuente: " + (["uv-calendar":"Horario oficial UV","university-browser":"Aula Virtual UV","university":"Aula Virtual UV","prisma-file":"Original PRISMA","canva-codex":"Canva","prisma-context":"Conversación de referencia"][resource.source] ?? resource.source)).font(.caption).foregroundStyle(ArosTheme.secondary)
                }.padding(20)
            }.navigationTitle(resource.kindTitle).inlineTitle()
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Menu {
                            if resource.editable { Button("Editar", systemImage: "pencil") { editing = true } }
                            Button("Eliminar", systemImage: "trash", role: .destructive) { confirmDelete = true }
                        } label: { Image(systemName: "ellipsis.circle") }
                    }
                }
                .sheet(isPresented: $editing) { ResourceEditor(resource: resource, area: resource.area) }
                .confirmationDialog("¿Eliminar «\(resource.title)» de Aros?", isPresented: $confirmDelete, titleVisibility: .visible) {
                    Button("Eliminar recurso y archivo", role: .destructive) {
                        Task {
                            do { try await store.deleteResource(resource); dismiss() }
                            catch { store.resourceError = error.localizedDescription }
                        }
                    }
                } message: { Text("El original en servicios externos se conserva.") }
        } else { ContentUnavailableView("Recurso no disponible", systemImage: "doc", description: Text("Puede haberse eliminado. Recarga tu espacio.")) }
    }
}

struct ResourceEditor: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let resource: Resource?
    @State private var draft: ResourceDraft
    @State private var hasDate: Bool
    @State private var date: Date
    @State private var confirmingEvent = false
    @State private var saving = false
    @State private var errorMessage: String?
    init(resource: Resource? = nil, area: Area = .personal, initialKind: String = "task") {
        self.resource = resource
        _draft = State(initialValue: resource.map(ResourceDraft.init) ?? ResourceDraft(kind: initialKind, area: area))
        _hasDate = State(initialValue: resource?.dueAt != nil || initialKind == "event")
        let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime,.withFractionalSeconds]
        let raw = resource?.dueAt ?? ""
        let parsed = format.date(from: raw); format.formatOptions = [.withInternetDateTime]
        _date = State(initialValue: parsed ?? format.date(from: raw) ?? .now)
    }
    var body: some View {
        NavigationStack {
            Form {
                if let errorMessage { Section { Text(errorMessage).foregroundStyle(.red) } }
                Section("Recurso") {
                    Picker("Tipo", selection: $draft.kind) {
                        ForEach(ResourceKind.names.keys.sorted(), id: \.self) { Text(ResourceKind.names[$0]!).tag($0) }
                    }.disabled(resource != nil)
                    Picker("Área", selection: $draft.area) { ForEach(Area.allCases) { Text($0.title).tag($0) } }
                    TextField("Título", text: $draft.title)
                    TextField("Descripción", text: $draft.description, axis: .vertical).lineLimit(3...8)
                }
                Section("Organizar") {
                    Toggle("Fecha", isOn: $hasDate)
                    if hasDate { DatePicker("Fecha y hora", selection: $date) }
                    Picker("Estado", selection: $draft.status) {
                        ForEach(ResourceKind.statuses.keys.sorted(), id: \.self) { Text(ResourceKind.statuses[$0]!).tag($0) }
                    }
                    Picker("Prioridad", selection: $draft.priority) {
                        Text("Normal").tag("normal"); Text("Alta").tag("high"); Text("Urgente").tag("urgent")
                    }
                }
                Section("Detalles") {
                    ForEach(detailFields, id: \.self) { key in
                        TextField(fieldLabel(key), text: Binding(get: { draft.data[key]?.text ?? "" }, set: { value in
                            if value.isEmpty { draft.data.removeValue(forKey: key) }
                            else if key == "amount", let number = Double(value.replacingOccurrences(of: ",", with: ".")) { draft.data[key] = .number(number) }
                            else { draft.data[key] = .string(value) }
                        }))
                    }
                    if draft.kind == "model" {
                        Toggle("Favorito", isOn: Binding(get: { draft.data["favorite"] == .bool(true) }, set: { draft.data["favorite"] = .bool($0) }))
                    }
                }
            }.navigationTitle(resource == nil ? "Añadir recurso" : "Editar").inlineTitle()
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancelar") { dismiss() }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(saving ? "Guardando…" : "Guardar") {
                            if draft.kind == "event" { confirmingEvent = true }
                            else { Task { await save() } }
                        }.disabled(saving || draft.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (draft.kind == "event" && !hasDate))
                    }
                }
                .confirmationDialog("Confirmar evento", isPresented: $confirmingEvent, titleVisibility: .visible) {
                    Button("Guardar evento en Aros") { Task { await save(confirmed: true) } }
                } message: { Text("\(draft.title)\n\(date.formatted())\nEl calendario externo no se modifica.") }
        }.interactiveDismissDisabled(saving)
    }
    private var detailFields: [String] {
        switch draft.kind {
        case "event": return ["location"]
        case "model": return ["url","material"]
        case "order": return ["store","amount","tracking_url"]
        case "expense": return ["amount"]
        case "contact": return ["role"]
        case "material": return ["quantity"]
        case "news","document","project": return ["url"]
        default: return []
        }
    }
    private func fieldLabel(_ key: String) -> String {
        ["location":"Lugar","url":"Enlace","material":"Material","store":"Tienda","amount":"Importe (€)","tracking_url":"Seguimiento","role":"Función","quantity":"Cantidad"][key] ?? key
    }
    private func save(confirmed: Bool = false) async {
        saving = true; errorMessage = nil; defer { saving = false }
        draft.dueAt = hasDate ? ISO8601DateFormatter().string(from: date) : nil
        do { try await store.saveResource(draft, id: resource?.id, confirmed: confirmed); dismiss() }
        catch { errorMessage = error.localizedDescription }
    }
}

struct RemoteInboxView: View {
    @Environment(AppStore.self) private var store
    @State private var showResolved = false
    var body: some View {
        List {
            Section { DemoLabel(); Toggle("Mostrar todos los estados", isOn: $showResolved) }
            let records = store.remoteInbox.filter { showResolved || ["new","seen"].contains($0.state) }
            if records.isEmpty { ContentUnavailableView("Todo al día", systemImage: "checkmark.circle") }
            ForEach(records) { item in
                VStack(alignment: .leading, spacing: 8) {
                    if let id = item.itemId {
                        NavigationLink(value: Destination.resource(id)) { Text(item.title).font(.headline) }
                    } else { Text(item.title).font(.headline) }
                    Text(item.reason).font(.caption).foregroundStyle(ArosTheme.secondary)
                    HStack {
                        Button(item.state == "resolved" ? "Reabrir" : "Resolver") {
                            Task { await store.performRemote { try await store.updateInbox(item.id, state: item.state == "resolved" ? "new" : "resolved") } }
                        }
                        Button("24 h") { Task { await store.performRemote { try await store.updateInbox(item.id, state: "snoozed") } } }
                        Button("Archivar") { Task { await store.performRemote { try await store.updateInbox(item.id, state: "archived") } } }
                    }.font(.caption).buttonStyle(.bordered)
                    Text(item.state).font(.caption2).foregroundStyle(ArosTheme.secondary)
                }.padding(.vertical, 7)
            }
        }.navigationTitle("Inbox").inlineTitle().refreshable { await store.refreshRemote() }
    }
}

@MainActor struct RemoteCalendarView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var textSize
    @State private var day = Date()
    @State private var month = Date()
    @State private var plan: RemoteCalendarPlan?
    @State private var range: RemoteCalendarRange?
    @State private var error: String?
    @State private var monthError: String?
    @State private var loading = false
    @State private var monthLoading = false
    @State private var creating = false
    @State private var availabilityOpen = false
    private var dates: NativeCalendarDates { NativeCalendarDates() }
    private var dayKey: String { dates.key(day) }
    private var monthKey: String { dates.key(dates.monthStart(month)) }
    private var agenda: [RemoteCalendarPlan.CalendarEntry] {
        (plan?.events ?? range?.events.filter { dates.overlaps($0, day: day) } ?? []).sorted { $0.start < $1.start }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                DemoLabel()
                monthPanel
                weekStrip
                agendaPanel
                if let plan {
                    DisclosureGroup(isExpanded: $availabilityOpen) {
                        VStack(alignment: .leading, spacing: 14) {
                            Text("Solapamientos").font(.subheadline.weight(.semibold))
                            if plan.conflicts.isEmpty { Text("No se cruzan los eventos guardados.").foregroundStyle(ArosTheme.secondary) }
                            ForEach(Array(plan.conflicts.enumerated()), id: \.offset) { _, conflict in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(timeRange(conflict.start, conflict.end)).font(.subheadline.weight(.medium))
                                    ForEach(conflict.ids, id: \.self) { id in
                                        if let event = plan.events.first(where: { $0.id == id }) {
                                            NavigationLink(value: Destination.resource(id)) { Label(event.title, systemImage: "arrow.up.right") }.frame(minHeight: 44)
                                        }
                                    }
                                }
                            }
                            Divider()
                            Text("Huecos · 08:00 a 22:00").font(.subheadline.weight(.semibold))
                            if plan.freeSlots.isEmpty { Text("No quedan huecos de 30 minutos.").foregroundStyle(ArosTheme.secondary) }
                            ForEach(Array(plan.freeSlots.enumerated()), id: \.offset) { _, slot in
                                HStack { Text(timeRange(slot.start, slot.end)).monospacedDigit(); Spacer(); Text("\(slot.minutes) min").foregroundStyle(ArosTheme.secondary) }
                            }
                            Text(plan.note).font(.caption).foregroundStyle(ArosTheme.secondary)
                        }.padding(.top, 12)
                    } label: { Label("Disponibilidad del día", systemImage: "clock").font(.subheadline.weight(.semibold)).frame(minHeight: 44) }
                    .padding(16).glass()
                }
                Text("Horario local · \(TimeZone.current.identifier). Los horarios oficiales de UV se consultan en modo de lectura.").font(.caption).foregroundStyle(ArosTheme.secondary)
            }.padding(18)
        }.background { ArosBackground() }
            .navigationTitle("Calendario").inlineTitle()
            .task(id: dayKey + "-\(store.remoteRevision)") { await loadPlan() }
            .task(id: monthKey + "-\(store.remoteRevision)") { await loadMonth() }
            .onChange(of: day) { _, value in month = value }
            .refreshable { await store.refreshRemote(); await reload() }
            .toolbar { ToolbarItem(placement: .primaryAction) { Button("Nuevo evento", systemImage: "plus") { creating = true } } }
            .sheet(isPresented: $creating, onDismiss: { Task { await reload() } }) { ResourceEditor(area: .personal, initialKind: "event") }
    }
    private var monthPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(dates.label(month, format: "MMMM yyyy").capitalized).font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                Button { selectMonth(-1) } label: { Image(systemName: "chevron.left").frame(width: 44, height: 44) }.accessibilityLabel("Mes anterior")
                Button { selectMonth(1) } label: { Image(systemName: "chevron.right").frame(width: 44, height: 44) }.accessibilityLabel("Mes siguiente")
            }
            HStack {
                DatePicker("Fecha", selection: $day, displayedComponents: .date).labelsHidden().accessibilityLabel("Elegir fecha del calendario")
                Spacer(); Button("Hoy") { day = .now; month = .now }.buttonStyle(.bordered).frame(minHeight: 44)
            }
            if textSize.isAccessibilitySize {
                Text("Selecciona una fecha con el selector para consultar tu agenda.").font(.caption).foregroundStyle(ArosTheme.secondary)
            } else {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 0), count: 7), spacing: 2) {
                    ForEach(Array(["L", "M", "X", "J", "V", "S", "D"].enumerated()), id: \.offset) { _, label in Text(label).font(.caption.weight(.semibold)).foregroundStyle(ArosTheme.secondary).accessibilityHidden(true) }
                    ForEach(dates.monthDays(month), id: \.self) { date in dayCell(date) }
                }
            }
            if monthLoading { HStack { ProgressView(); Text("Actualizando el mes…").font(.caption).foregroundStyle(ArosTheme.secondary) } }
            if let monthError { Text(monthError).font(.caption).foregroundStyle(.red); Button("Reintentar el mes") { Task { await loadMonth() } }.frame(minHeight: 44) }
        }.padding(14).glass()
    }
    private func dayCell(_ date: Date) -> some View {
        let selected = dates.calendar.isDate(date, inSameDayAs: day)
        let today = dates.calendar.isDateInToday(date)
        let entries = range?.events.filter { dates.overlaps($0, day: date) } ?? []
        return Button { day = date } label: {
            VStack(spacing: 4) {
                Text(dates.label(date, format: "d")).font(.subheadline.weight(selected || today ? .bold : .regular)).monospacedDigit()
                HStack(spacing: 3) {
                    ForEach(Array(Set(entries.map(\.area))).sorted { $0.rawValue < $1.rawValue }.prefix(3), id: \.self) { area in Circle().fill(selected ? Color.white : area.tint).frame(width: 4, height: 4) }
                }.frame(height: 4)
            }.frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(selected ? Color.white : dates.calendar.isDate(date, equalTo: month, toGranularity: .month) ? ArosTheme.ink : ArosTheme.secondary)
                .background(selected ? Area.university.tint : today ? Area.university.tint.opacity(0.12) : Color.clear, in: RoundedRectangle(cornerRadius: 12))
        }.buttonStyle(.plain)
            .accessibilityLabel(dates.label(date, format: "EEEE d MMMM") + (today ? ", hoy" : "") + (monthLoading ? ", actualizando eventos" : ", \(entries.count) eventos"))
            .accessibilityAddTraits(selected ? .isSelected : [])
    }
    private var weekStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(dates.weekDays(day), id: \.self) { date in
                    let selected = dates.calendar.isDate(date, inSameDayAs: day)
                    Button { day = date } label: {
                        VStack(spacing: 6) { Text(dates.label(date, format: "EEE")).font(.caption); Text(dates.label(date, format: "d")).font(.headline).monospacedDigit() }
                            .frame(minWidth: textSize.isAccessibilitySize ? 70 : 44, minHeight: 64).padding(.horizontal, 4)
                            .foregroundStyle(selected ? Color.white : ArosTheme.ink)
                            .background(selected ? Area.university.tint : Color.white.opacity(0.8), in: RoundedRectangle(cornerRadius: 14))
                    }.buttonStyle(.plain).accessibilityLabel(dates.label(date, format: "EEEE d MMMM")).accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
        }.accessibilityLabel("Semana seleccionada")
    }
    private var agendaPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(dates.label(day, format: "EEEE d 'de' MMMM").capitalized).font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            if loading { ProgressView("Consultando tu día…") }
            else if let error {
                Label(error, systemImage: "exclamationmark.circle").font(.subheadline).foregroundStyle(.red)
                Button("Reintentar") { Task { await loadPlan() } }.buttonStyle(.bordered).frame(minHeight: 44)
            } else {
                HStack { Label("\(agenda.count) eventos", systemImage: "calendar"); if let plan { Spacer(); Text("\(plan.conflicts.count) cruces").foregroundStyle(ArosTheme.secondary) } }.font(.caption)
                if agenda.isEmpty {
                    VStack(alignment: .leading, spacing: 8) { Label("Un día sin eventos", systemImage: "sun.max").font(.headline); Text("Tu agenda está libre para esta fecha. Puedes añadir un evento o explorar otros días.").font(.subheadline).foregroundStyle(ArosTheme.secondary) }.padding(.vertical, 12)
                }
                ForEach(agenda) { event in agendaRow(event) }
            }
        }.padding(16).glass()
    }
    private func agendaRow(_ event: RemoteCalendarPlan.CalendarEntry) -> some View {
        let metadata = range?.events.first { $0.id == event.id } ?? event
        let exam = metadata.category.map { $0 == "exam" } ?? metadata.title.localizedCaseInsensitiveContains("examen")
        let tint = exam ? Color(red: 196 / 255.0, green: 92 / 255.0, blue: 74 / 255.0) : event.area.tint
        return NavigationLink(value: Destination.resource(event.id)) {
            HStack(alignment: .top, spacing: 12) {
                RoundedRectangle(cornerRadius: 3).fill(tint).frame(width: 4)
                VStack(alignment: .leading, spacing: 6) {
                    Text(event.allDay ? "Todo el día" : timeRange(event.start, event.end)).font(.caption.weight(.medium)).monospacedDigit().foregroundStyle(ArosTheme.secondary)
                    Text(event.title).font(.subheadline.weight(.semibold)).foregroundStyle(ArosTheme.ink).fixedSize(horizontal: false, vertical: true)
                    Label(exam ? "Examen · \(event.area.title)" : event.area.title, systemImage: exam ? "pencil.and.outline" : event.area.symbol).font(.caption).foregroundStyle(tint)
                    if let location = metadata.location, !location.isEmpty { Label(location, systemImage: "mappin.and.ellipse").font(.caption).foregroundStyle(ArosTheme.secondary) }
                    if event.estimatedDuration { Text("Duración estimada: 60 minutos").font(.caption2).foregroundStyle(ArosTheme.secondary) }
                }
                Spacer(minLength: 0); Image(systemName: "chevron.right").font(.caption).foregroundStyle(ArosTheme.secondary)
            }.padding(12).frame(minHeight: 44).background(tint.opacity(0.06), in: RoundedRectangle(cornerRadius: 14))
        }.buttonStyle(.plain)
    }
    private func selectMonth(_ offset: Int) {
        if let next = dates.calendar.date(byAdding: .month, value: offset, to: day) { day = next; month = next }
    }
    private func timeRange(_ start: Date, _ end: Date) -> String {
        dates.timeRange(start, end)
    }
    private func reload() async { async let daily: Void = loadPlan(); async let monthly: Void = loadMonth(); _ = await (daily, monthly) }
    private func loadPlan() async {
        guard let client = store.apiClient else { return }
        let revision = store.remoteRevision, requestedDay = dayKey
        var query = URLComponents(); query.queryItems = [URLQueryItem(name: "day", value: requestedDay), URLQueryItem(name: "zone", value: TimeZone.current.identifier)]
        loading = true; error = nil; plan = nil
        do {
            let result: RemoteCalendarPlan = try await client.request("/calendar/day?" + (query.percentEncodedQuery ?? ""))
            guard !Task.isCancelled, revision == store.remoteRevision, requestedDay == dayKey else { return }
            plan = result; loading = false
        } catch {
            guard !Task.isCancelled, revision == store.remoteRevision, requestedDay == dayKey else { return }
            self.error = error.localizedDescription; loading = false
        }
    }
    private func loadMonth() async {
        guard let client = store.apiClient else { return }
        let revision = store.remoteRevision, requestedMonth = monthKey, bounds = dates.monthBounds(month)
        var query = URLComponents(); query.queryItems = [URLQueryItem(name: "start", value: dates.key(bounds.start)), URLQueryItem(name: "end", value: dates.key(bounds.end)), URLQueryItem(name: "zone", value: TimeZone.current.identifier)]
        monthLoading = true; monthError = nil; range = nil
        do {
            let result: RemoteCalendarRange = try await client.request("/calendar/range?" + (query.percentEncodedQuery ?? ""))
            guard !Task.isCancelled, revision == store.remoteRevision, requestedMonth == monthKey else { return }
            range = result; monthLoading = false
        } catch {
            guard !Task.isCancelled, revision == store.remoteRevision, requestedMonth == monthKey else { return }
            monthError = error.localizedDescription; monthLoading = false
        }
    }
}

@MainActor struct PrintingDiscoveryView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var textSize
    @State private var feed: RemotePrintingFeed?
    @State private var collection: [RemotePrintRecommendation] = []
    @State private var loadedSelection: PrintingCollectionQuery?
    @State private var nextOffset: Int?
    @State private var resultTotal = 0
    @State private var query = ""
    @State private var category = "all"
    @State private var savedOnly = false
    @State private var error: String?
    @State private var busy = false
    @State private var requestID = UUID()
    @State private var refreshQueued = false
    @State private var favorites: [String: Bool] = [:]
    @State private var saving = Set<String>()
    @State private var statusOpen = false
    private var updating: Bool { refreshQueued || feed?.status.updating == true }
    private var selection: PrintingCollectionQuery { PrintingCollectionQuery(text: query, category: category, favorite: savedOnly) }
    private var tags: [String] { ["química", "PRISMA", "tecnología", "organización"] }
    private var visible: [RemotePrintRecommendation] { loadedSelection == selection ? collection.filter { !savedOnly || isFavorite($0) } : [] }
    private func isFavorite(_ item: RemotePrintRecommendation) -> Bool { favorites[item.id] ?? (item.favorite == true) }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header
                collectionControls
                if let error {
                    VStack(alignment: .leading, spacing: 8) { Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.red); Button("Reintentar") { Task { _ = await load() } }.frame(minHeight: 44) }.font(.subheadline).padding(14).glass()
                }
                if (busy || loadedSelection != selection) && visible.isEmpty && error == nil { ProgressView("Consultando tu colección…").frame(maxWidth: .infinity).padding(30) }
                else if loadedSelection == selection && visible.isEmpty && error == nil { emptyCollection }
                if !visible.isEmpty {
                    HStack { Text(savedOnly ? "Tus ideas guardadas" : "Explora la colección").font(.headline); Spacer(); Text("\(visible.count) de \(resultTotal)").font(.subheadline).monospacedDigit().foregroundStyle(ArosTheme.secondary) }
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 14, alignment: .top), count: textSize.isAccessibilitySize ? 1 : 2), spacing: 18) {
                        ForEach(visible) { item in
                            PrintingCollectionCard(item: item, favorite: isFavorite(item), saving: saving.contains(item.id), onFavorite: { Task { _ = await toggleFavorite(item) } }) {
                                PrintingRecommendationDetail(item: item, favorite: Binding(get: { isFavorite(item) }, set: { _ in }), onFavorite: { await toggleFavorite(item) })
                            }
                        }
                    }
                }
                if nextOffset != nil && loadedSelection == selection {
                    Button { Task { _ = await load(append: true) } } label: { HStack { if busy { ProgressView().controlSize(.small) }; Text(busy ? "Cargando…" : "Cargar más ideas") }.font(.subheadline.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 44) }.buttonStyle(.bordered).disabled(busy)
                }
                NavigationLink { ResourceListView(area: .printing) } label: {
                    HStack { Label("Mis proyectos y recursos", systemImage: "folder"); Spacer(); Image(systemName: "chevron.right").font(.caption) }.font(.subheadline.weight(.medium)).padding(16).frame(minHeight: 44).glass()
                }.buttonStyle(.plain)
            }.padding(18)
        }.background { ArosBackground() }.navigationTitle("Impresión 3D").inlineTitle()
            .task(id: selection.path() + "-\(store.remoteRevision)") {
                do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
                guard !Task.isCancelled else { return }; _ = await load()
            }
            .refreshable { _ = await load() }
            .task(id: "\(updating)-\(store.remoteRevision)-\(error == nil)") {
                guard updating, error == nil else { return }
                while !Task.isCancelled && updating {
                    do { try await Task.sleep(for: .seconds(3)) } catch { return }
                    guard !Task.isCancelled else { return }
                    let loaded = await load(statusOnly: true)
                    if !loaded && (Task.isCancelled || error != nil) { return }
                }
            }
    }
    private var header: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Label("TU COLECCIÓN", systemImage: "cube.transparent").font(.caption.weight(.semibold)).foregroundStyle(Area.printing.tint)
                    Text("Ideas para imprimir").font(.title2.bold()).foregroundStyle(ArosTheme.ink)
                }
                Spacer(minLength: 0)
                Button { Task { await discover() } } label: { Image(systemName: "arrow.clockwise").frame(width: 44, height: 44).background(Area.printing.tint.opacity(0.10), in: Circle()) }.buttonStyle(.plain).foregroundStyle(Area.printing.tint).disabled(busy || updating || store.apiClient == nil).accessibilityLabel("Buscar nuevas ideas")
            }
            Text("Fotos reales y proyectos para tus intereses. Guarda lo que quieras explorar después.").font(.subheadline).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                if updating { ProgressView().controlSize(.small) } else { Image(systemName: feed?.status.enabled == false ? "pause.circle" : "checkmark.circle").foregroundStyle(Area.printing.tint) }
                Text(updating ? (feed?.status.running == true ? "Buscando nuevas ideas…" : "Búsqueda en cola") : feed?.status.enabled == false ? "Actualización automática pausada" : feed == nil ? "Consultando colección" : "\(feed?.status.total ?? resultTotal) ideas · búsqueda cada 12 h").font(.caption.weight(.medium)).foregroundStyle(ArosTheme.secondary)
            }.accessibilityElement(children: .combine)
            if let status = feed?.status {
                DisclosureGroup(isExpanded: $statusOpen) {
                    VStack(alignment: .leading, spacing: 8) {
                        if let date = status.lastRefresh { Text("Última búsqueda: \(displayDate(date))") }
                        if let date = status.nextRefresh { Text("Próxima búsqueda: \(displayDate(date))") }
                        if let count = status.discovered { Text("\(count) novedades en la última búsqueda") }
                        Text("Objetivo: 10 ideas nuevas cada 12 horas, según lo disponible en las fuentes.")
                        if let deficit = status.deficit, deficit > 0 { Text("Faltan \(deficit) ideas para el objetivo de este ciclo.") }
                        if let issue = status.error, !issue.isEmpty { Label(issue, systemImage: "exclamationmark.circle").foregroundStyle(.red) }
                    }.font(.caption).foregroundStyle(ArosTheme.secondary).padding(.top, 8)
                } label: { Text(status.error?.isEmpty == false ? "Revisar actualización" : "Estado de actualización").font(.caption).foregroundStyle(ArosTheme.secondary).frame(minHeight: 44) }
            }
        }.padding(18).glass()
    }
    private var collectionControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass").foregroundStyle(ArosTheme.secondary)
                TextField("Buscar en toda la colección", text: $query).font(.subheadline).autocorrectionDisabled().frame(minHeight: 44)
                if !query.isEmpty { Button { query = "" } label: { Image(systemName: "xmark.circle.fill").frame(width: 44, height: 44) }.buttonStyle(.plain).foregroundStyle(ArosTheme.secondary).accessibilityLabel("Borrar búsqueda") }
            }.padding(.horizontal, 14).frame(minHeight: 50).glass()
            Picker("Colección", selection: $savedOnly) { Text("Todas las ideas").tag(false); Text("Guardadas").tag(true) }.pickerStyle(.segmented).frame(minHeight: 44)
            if textSize.isAccessibilitySize {
                Picker("Categoría", selection: $category) { Text("Todas las categorías").tag("all"); ForEach(tags, id: \.self) { Text($0.capitalized).tag($0) } }.frame(minHeight: 44)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) { tagButton("all", label: "Todo"); ForEach(tags, id: \.self) { tag in tagButton(tag, label: tag == "PRISMA" ? tag : tag.capitalized) } }
                }
            }
        }
    }
    private func tagButton(_ tag: String, label: String) -> some View {
        Button { category = tag } label: { Text(label).font(.caption.weight(.semibold)).padding(.horizontal, 14).frame(minHeight: 44).background(category == tag ? Area.printing.tint.opacity(0.13) : Color.white.opacity(0.8), in: Capsule()).foregroundStyle(category == tag ? Area.printing.tint : ArosTheme.secondary) }.buttonStyle(.plain).accessibilityAddTraits(category == tag ? .isSelected : [])
    }
    private var emptyCollection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: savedOnly ? "heart" : "cube.transparent").font(.largeTitle).foregroundStyle(Area.printing.tint).accessibilityHidden(true)
            Text(savedOnly && query.isEmpty && category == "all" ? "Tu colección empieza aquí" : query.isEmpty && category == "all" ? "Aún no hay ideas" : "No encontramos coincidencias").font(.headline)
            Text(savedOnly ? "Guarda ideas con el corazón para encontrarlas aquí." : "Prueba otra búsqueda o consulta las próximas novedades.").font(.subheadline).foregroundStyle(ArosTheme.secondary)
            if !query.isEmpty || category != "all" { Button("Limpiar filtros") { query = ""; category = "all" }.frame(minHeight: 44) }
        }.padding(20).frame(maxWidth: .infinity, alignment: .leading).glass()
    }
    private func load(append: Bool = false, statusOnly: Bool = false) async -> Bool {
        guard let client = store.apiClient else { return false }
        if statusOnly && busy { return true }
        if append && (busy || loadedSelection != selection || nextOffset == nil) { return false }
        let revision = store.remoteRevision, requested = UUID(), requestedSelection = selection
        let offset = append ? (nextOffset ?? 0) : 0
        requestID = requested; busy = true
        defer { if requestID == requested { busy = false } }
        do {
            let result: RemotePrintingFeed = try await client.request(requestedSelection.path(offset: offset, limit: statusOnly ? 1 : 60))
            guard !Task.isCancelled, revision == store.remoteRevision, requestID == requested, requestedSelection == selection else { return false }
            if statusOnly && updating && !result.status.updating { return await load() }
            feed = result; refreshQueued = false; error = nil
            if !statusOnly {
                if append {
                    var seen = Set(collection.map(\.id))
                    collection.append(contentsOf: result.items.filter { seen.insert($0.id).inserted })
                } else { collection = result.items }
                loadedSelection = requestedSelection
                resultTotal = result.pagination?.total ?? collection.count
                nextOffset = result.pagination?.nextOffset
                for item in result.items where favorites[item.id] == item.favorite { favorites.removeValue(forKey: item.id) }
            }
            return true
        } catch {
            guard !Task.isCancelled, revision == store.remoteRevision, requestID == requested, requestedSelection == selection else { return false }
            self.error = error.localizedDescription; return false
        }
    }
    private func discover() async {
        guard !busy, !updating, let client = store.apiClient else { return }
        let revision = store.remoteRevision
        busy = true; error = nil
        do {
            let result: RemotePrintingRefresh = try await client.request("/printing/refresh", method: "POST", body: [:])
            guard !Task.isCancelled, revision == store.remoteRevision else { busy = false; return }
            refreshQueued = result.queued == true || result.running == true
            busy = false; _ = await load()
        } catch { if revision == store.remoteRevision { self.error = error.localizedDescription }; busy = false }
    }
    private func toggleFavorite(_ item: RemotePrintRecommendation) async -> String? {
        guard !saving.contains(item.id), let client = store.apiClient else { return nil }
        let revision = store.remoteRevision, wanted = !isFavorite(item)
        saving.insert(item.id)
        defer { saving.remove(item.id) }
        do {
            let id = item.id.addingPercentEncoding(withAllowedCharacters: CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))) ?? item.id
            let result: RemotePrintingFavorite = try await client.request("/printing/recommendations/\(id)/favorite", method: "PATCH", body: ["favorite": wanted])
            guard !Task.isCancelled, revision == store.remoteRevision else { return nil }
            guard result.id == item.id else { throw APIError(message: "No se pudo confirmar la idea guardada.", status: 0) }
            favorites[item.id] = result.favorite; error = nil
            if savedOnly { _ = await load() }
            return nil
        } catch { guard revision == store.remoteRevision else { return nil }; self.error = error.localizedDescription; return error.localizedDescription }
    }
}
struct PrintingCollectionCard<Detail: View>: View {
    @Environment(\.dynamicTypeSize) private var textSize
    let item: RemotePrintRecommendation
    let favorite: Bool
    let saving: Bool
    let onFavorite: () -> Void
    @ViewBuilder let detail: () -> Detail
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack(alignment: .topTrailing) {
                NavigationLink(destination: detail) {
                    ZStack(alignment: .bottomLeading) {
                        RecommendationPhoto(url: item.photos.first, title: item.title).frame(height: textSize.isAccessibilitySize ? 260 : 190).frame(maxWidth: .infinity).clipped()
                        Text(item.source).font(.caption2.weight(.medium)).padding(.horizontal, 8).padding(.vertical, 5).background(.regularMaterial, in: Capsule()).foregroundStyle(ArosTheme.ink).padding(10)
                    }
                }.buttonStyle(.plain).accessibilityLabel("Ver \(item.title)")
                Button(action: onFavorite) {
                    Group { if saving { ProgressView().controlSize(.small) } else { Image(systemName: favorite ? "heart.fill" : "heart").foregroundStyle(favorite ? Color(red: 0.75, green: 0.28, blue: 0.37) : ArosTheme.ink) } }.frame(width: 44, height: 44).background(.regularMaterial, in: Circle())
                }.buttonStyle(.plain).disabled(saving).padding(8).accessibilityLabel(favorite ? "Quitar \(item.title) de guardadas" : "Guardar \(item.title)").accessibilityValue(favorite ? "Guardado" : "Sin guardar")
            }
            NavigationLink(destination: detail) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(item.title).font(.subheadline.weight(.semibold)).foregroundStyle(ArosTheme.ink).lineLimit(textSize.isAccessibilitySize ? nil : 3).fixedSize(horizontal: false, vertical: true)
                    Text(item.spanishSummary).font(.caption).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
                    if let tag = item.profileTags.first { Text(tag == "PRISMA" ? tag : tag.capitalized).font(.caption2.weight(.medium)).foregroundStyle(Area.printing.tint) }
                    if item.photos.count > 1 { Label("\(item.photos.count) fotos", systemImage: "photo.on.rectangle").font(.caption2).foregroundStyle(ArosTheme.secondary) }
                }.padding(13).frame(maxWidth: .infinity, alignment: .leading)
            }.buttonStyle(.plain)
        }.glass().clipShape(RoundedRectangle(cornerRadius: 20))
    }
}
struct RecommendationPhoto: View {
    let url: URL?
    let title: String
    var body: some View {
        ZStack {
            Color(red: 0.94, green: 0.96, blue: 0.95)
            AsyncImage(url: url) { phase in
                if let image = phase.image { image.resizable().scaledToFit() }
                else if phase.error != nil || url == nil { VStack(spacing: 8) { Image(systemName: "photo"); Text("Foto no disponible").font(.caption) }.foregroundStyle(ArosTheme.secondary).frame(maxWidth: .infinity, maxHeight: .infinity) }
                else { ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity) }
            }
        }.accessibilityLabel("Foto del objeto: \(title)")
    }
}
struct PrintingRecommendationDetail: View {
    @Environment(\.dynamicTypeSize) private var textSize
    let item: RemotePrintRecommendation
    @Binding var favorite: Bool
    let onFavorite: () async -> String?
    @State private var saving = false
    @State private var error: String?
    @State private var selectedPhoto = 0
    @State private var showPhoto = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                photoGallery
                VStack(alignment: .leading, spacing: 12) {
                    Text(item.title).font(.title2.bold()).foregroundStyle(ArosTheme.ink).fixedSize(horizontal: false, vertical: true)
                    Text(item.spanishSummary).font(.subheadline).foregroundStyle(ArosTheme.secondary)
                    if textSize.isAccessibilitySize {
                        VStack(alignment: .leading, spacing: 8) { Label(item.source, systemImage: "globe"); saveButton }.font(.subheadline).foregroundStyle(Area.printing.tint)
                    } else {
                        HStack { Label(item.source, systemImage: "globe"); Spacer(); saveButton }.font(.subheadline).foregroundStyle(Area.printing.tint)
                    }
                    if !item.profileTags.isEmpty { Text(item.profileTags.map { $0 == "PRISMA" ? $0 : $0.capitalized }.joined(separator: " · ")).font(.caption.weight(.medium)).foregroundStyle(Area.printing.tint) }
                    if let error { Label(error, systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(.red) }
                }.padding(18).glass()
                if let url = RemotePrintRecommendation.safeURL(item.sourceUrl) {
                    Link(destination: url) { HStack { Label("Explorar proyecto original", systemImage: "arrow.up.right.square"); Spacer(); Image(systemName: "chevron.right") }.font(.subheadline.weight(.semibold)).padding(16).frame(minHeight: 44).frame(maxWidth: .infinity).background(Area.printing.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 16)) }.foregroundStyle(Area.printing.tint)
                    ShareLink(item: url) { Label("Compartir esta idea", systemImage: "square.and.arrow.up").frame(minHeight: 44) }.font(.subheadline)
                }
                VStack(alignment: .leading, spacing: 12) {
                    Label("Licencia y fuente", systemImage: "doc.text.magnifyingglass").font(.headline)
                    Text(item.license ?? "No indicada; consulta la ficha original").font(.subheadline).foregroundStyle(ArosTheme.secondary)
                    if let url = RemotePrintRecommendation.safeURL(item.licenseUrl) { Link("Consultar licencia en la fuente", destination: url).font(.subheadline).frame(minHeight: 44) }
                    if let date = item.publishedAt { Text("Publicado: \(displayDate(date))").font(.caption).foregroundStyle(ArosTheme.secondary) }
                    Text("En tu colección desde: \(displayDate(item.recommendedAt))").font(.caption).foregroundStyle(ArosTheme.secondary)
                    Text("Dimensiones, materiales y montaje: consulta la ficha del autor.").font(.caption).foregroundStyle(ArosTheme.secondary)
                }.padding(18).glass()
                DisclosureGroup("Detalles del proyecto") {
                    VStack(alignment: .leading, spacing: 12) { if !item.usage.isEmpty { Text("Uso sugerido").font(.caption.weight(.semibold)); Text(item.usage) }; if !item.description.isEmpty && item.description != item.usage { Text("Descripción del autor").font(.caption.weight(.semibold)); Text(item.description) } }.font(.subheadline).foregroundStyle(ArosTheme.secondary).textSelection(.enabled).padding(.top, 10)
                }.padding(18).glass()
            }.padding(18)
        }.background { ArosBackground() }.navigationTitle("Tu idea para imprimir").inlineTitle()
            .sheet(isPresented: $showPhoto) {
                NavigationStack { RecommendationPhoto(url: item.photos.indices.contains(selectedPhoto) ? item.photos[selectedPhoto] : nil, title: item.title).padding(14).background { ArosBackground() }.navigationTitle("Foto del objeto").inlineTitle().toolbar { ToolbarItem(placement: .confirmationAction) { Button("Cerrar") { showPhoto = false } } } }
            }
    }
    private var photoGallery: some View {
        VStack(alignment: .leading, spacing: 10) {
            if item.photos.isEmpty { RecommendationPhoto(url: nil, title: item.title).frame(height: 280).glass() }
            else {
                Button { showPhoto = true } label: { RecommendationPhoto(url: item.photos[selectedPhoto], title: item.title).frame(height: textSize.isAccessibilitySize ? 300 : 320).glass() }.buttonStyle(.plain).accessibilityLabel("Ampliar foto \(selectedPhoto + 1) de \(item.photos.count)")
                if item.photos.count > 1 {
                    ScrollView(.horizontal, showsIndicators: false) { HStack(spacing: 10) { ForEach(Array(item.photos.enumerated()), id: \.offset) { index, url in Button { selectedPhoto = index } label: { RecommendationPhoto(url: url, title: item.title).frame(width: 72, height: 64).clipShape(RoundedRectangle(cornerRadius: 12)).overlay(RoundedRectangle(cornerRadius: 12).stroke(selectedPhoto == index ? Area.printing.tint : .clear, lineWidth: 2)) }.buttonStyle(.plain).accessibilityLabel("Foto \(index + 1) de \(item.photos.count)").accessibilityAddTraits(selectedPhoto == index ? .isSelected : []) } } }
                }
                Text("\(selectedPhoto + 1) de \(item.photos.count) fotos · toca para ampliar").font(.caption).foregroundStyle(ArosTheme.secondary)
            }
        }
    }
    private var saveButton: some View {
        Button { Task { await save() } } label: { Label(saving ? "Guardando…" : favorite ? "Guardada" : "Guardar", systemImage: favorite ? "heart.fill" : "heart").frame(minHeight: 44) }.disabled(saving).accessibilityValue(favorite ? "Guardado" : "Sin guardar")
    }
    private func save() async { guard !saving else { return }; saving = true; error = await onFavorite(); saving = false }
}

struct PrismaLibraryView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var library: RemotePrismaLibrary?
    @State private var applicationOpen = false
    @State private var clientID = ""
    @State private var clientSecret = ""
    @State private var brief = ""
    @State private var reference = ""
    @State private var busy = false
    @State private var error: String?
    @State private var notice: String?
    private var pendingDesign: Bool { library?.assets.contains { $0.source == "canva-request" && ["pending", "active"].contains($0.status) } == true }
    var body: some View {
        List {
            Section("Canva") {
                Text("Canva conectado en Codex autoriza a Codex. Aros necesita su propia aplicación OAuth y tu autorización.").font(.caption).foregroundStyle(ArosTheme.secondary)
                Text("La autorización se completa en el navegador. Inicia sesión en Aros web con tu misma cuenta antes de conectar Canva.").font(.caption).foregroundStyle(ArosTheme.secondary)
                DisclosureGroup("Configurar aplicación Canva", isExpanded: $applicationOpen) {
                    Link("Portal de Canva Developers", destination: URL(string: "https://www.canva.com/developers/apps")!)
                    Text("Activa las API REST en Outside Canva y registra la dirección de retorno que muestra Aros.").font(.caption)
                    if let callback = library?.canva.redirectUri { Text(callback).font(.caption).textSelection(.enabled) }
                    if let scopes = library?.canva.scopes, !scopes.isEmpty { Text("Permisos: \(scopes.joined(separator: ", "))").font(.caption) }
                    TextField("Client ID", text: $clientID).autocorrectionDisabled()
                    SecureField("Client secret", text: $clientSecret).autocorrectionDisabled()
                    Text("Introduce el secreto aquí, nunca en el chat. No se guarda en el dispositivo y se limpia después de cada intento.").font(.caption).foregroundStyle(ArosTheme.secondary)
                    Button("Guardar aplicación OAuth") { Task { await saveApplication() } }.disabled(busy || clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || clientSecret.count < 8 || library?.canva.redirectUri == nil)
                }.onChange(of: applicationOpen) { _, open in if !open { clientSecret = "" } }

                Text(library?.canva.connected == true ? "Canva autorizado" : library?.canva.configured == true ? "Falta autorizar tu cuenta de Canva" : "Configura una aplicación Canva autorizada en Aros web")
                if library?.canva.configured == true && library?.canva.connected == false { Button("Autorizar Canva en Aros web") { Task { await authorize() } }.disabled(busy) }
                Button("Sincronizar diseños") { Task { await synchronize() } }.disabled(busy || library?.canva.connected != true)
            }
            Section("Carteles, rascas, pegatinas y referencias") {
                if library?.assets.isEmpty == true { Text("Todavía no hay materiales importados.").foregroundStyle(ArosTheme.secondary) }
                ForEach(PrismaAssetOrder.ordered(library?.assets ?? [])) { asset in
                    VStack(alignment: .leading, spacing: 8) {
                        if asset.data["category"]?.text == "context" {
                            Label("Referencia histórica", systemImage: "text.book.closed").font(.caption).foregroundStyle(ArosTheme.secondary)
                            DisclosureGroup("Leer referencia completa") { Text(asset.description).font(.subheadline).textSelection(.enabled) }
                        } else if asset.data["preview_item_id"]?.text.isEmpty == false || ["pdf", "png", "jpg", "jpeg"].contains((asset.data["filename"]?.text ?? "").split(separator: ".").last.map(String.init)?.lowercased() ?? "") { PrismaAssetPreview(id: asset.id).frame(height: 140) }
                        if asset.data["filename"]?.text.isEmpty == false { PrismaOriginalActions(asset: asset) }
                        NavigationLink(value: Destination.resource(asset.id)) { Text(asset.title).font(.headline) }
                        if asset.data["review_state"]?.text == "needs_review" { Label("Pendiente de revisar", systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(Color(red: 0.5, green: 0.35, blue: 0.1)) }
                        if asset.data["review_state"]?.text == "approved" { Label("Referencia aprobada", systemImage: "checkmark.seal").font(.caption).foregroundStyle(.green) }
                        Text(asset.description).font(.caption).lineLimit(3)
                        if let state = asset.data["state"]?.text { Text("Estado: \(state)").font(.caption).foregroundStyle(ArosTheme.secondary) }
                        if let issue = asset.data["error"]?.text, !issue.isEmpty { Text(issue).font(.caption).foregroundStyle(.red) }
                        if let value = asset.data["canva_edit_url"]?.text, let url = URL(string: value), url.scheme == "https" { Link("Editar en Canva", destination: url) }
                    }.padding(.vertical, 6)
                }
            }
            Section("Preparar un diseño") {
                if pendingDesign { Text("Diseño en preparación · estado actualizado cada 3 segundos").font(.caption) }
                TextField("Formato, objetivo, textos y estilo…", text: $brief, axis: .vertical).lineLimit(3...8)
                Picker("Referencia", selection: $reference) {
                    Text("Sin referencia").tag("")
                    ForEach((library?.assets ?? []).filter { $0.source != "canva-request" }) { Text($0.title).tag($0.id) }
                }
                Text("Esta solicitud usa el modelo de diseño configurado por el operador. El resto de Aros mantiene tu modelo de Ajustes.").font(.caption).foregroundStyle(ArosTheme.secondary)
                Button("Solicitar diseño avanzado") { Task { await design() } }.disabled(busy || pendingDesign || library?.canva.connected != true || library?.canva.designAvailable != true || store.remoteUser?.aiConsent != true || brief.trimmingCharacters(in: .whitespacesAndNewlines).count < 10)
                if library?.canva.connected != true { Text("Autoriza Canva para crear una propuesta editable.").font(.caption) }
                if library?.canva.designAvailable != true { Text("El operador debe configurar una conexión de agentes y un modelo de diseño para habilitar esta función.").font(.caption) }
                if store.remoteUser?.aiConsent != true { Text("Autoriza el uso de IA externa en Ajustes para solicitar un diseño.").font(.caption) }
            }
            Section { NavigationLink { ResourceListView(area: .prisma) } label: { Label("Todos mis recursos de PRISMA", systemImage: "folder") } }
            if busy { ProgressView("Consultando Canva…") }
            if let notice { Text(notice).font(.caption) }
            if let issue = error ?? library?.canva.error { Text(issue).foregroundStyle(.red) }
        }.navigationTitle("PRISMA").inlineTitle().task { await load() }.refreshable { await load() }
            .task(id: pendingDesign) {
                guard pendingDesign else { return }
                while !Task.isCancelled && pendingDesign {
                    do { try await Task.sleep(for: .seconds(3)) } catch { return }
                    await load()
                }
            }
    }
    private func load() async { guard let client = store.apiClient else { return }; do { library = try await client.request("/prisma/library") } catch { self.error = error.localizedDescription } }
    private func saveApplication() async {
        guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false; clientSecret = "" }
        do {
            let _: JSONValue = try await client.request("/prisma/canva/application", method: "POST", body: ["client_id":clientID.trimmingCharacters(in: .whitespacesAndNewlines),"client_secret":clientSecret.trimmingCharacters(in: .whitespacesAndNewlines)])
            clientID = ""; applicationOpen = false; notice = "Aplicación guardada. Autoriza ahora tu cuenta de Canva en Aros."
            await load()
        } catch { self.error = error.localizedDescription }
    }
    private func authorize() async {
        guard let client = store.apiClient else { return }
        var components = URLComponents(url: client.baseURL, resolvingAgainstBaseURL: false)
        components?.fragment = "prisma"
        if let url = components?.url { openURL(url) }
    }
    private func synchronize() async {
        guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false }
        do { let _: JSONValue = try await client.request("/prisma/canva/sync", method: "POST", body: [:]); await load() } catch { self.error = error.localizedDescription }
    }
    private func design() async {
        guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false }
        do {
            var body: [String: Any] = ["brief":brief]
            if !reference.isEmpty { body["asset_id"] = reference }
            let result: Resource = try await client.request("/prisma/canva/design", method: "POST", body: body)
            notice = result.data["state"]?.text == "blocked" ? "Solicitud bloqueada: revisa Canva." : "Solicitud en cola. El enlace editable aparecerá cuando termine."
            brief = ""; await load()
        } catch { self.error = error.localizedDescription }
    }
}
struct PrismaAssetPreview: View {
    @Environment(AppStore.self) private var store
    let id: String
    @State private var content: Data?
    var body: some View {
        Group {
            #if os(iOS)
            if let content, let image = UIImage(data: content) { Image(uiImage: image).resizable().scaledToFit() } else { placeholder }
            #else
            if let content, let image = NSImage(data: content) { Image(nsImage: image).resizable().scaledToFit() } else { placeholder }
            #endif
        }.accessibilityLabel("Vista previa del material")
            .task(id: id) {
                guard let client = store.apiClient else { return }
                var request = URLRequest(url: client.baseURL.appendingPathComponent("api/v1/prisma/assets/\(id)/preview"))
                request.setValue("Bearer \(client.token)", forHTTPHeaderField: "Authorization")
                if let (data,response) = try? await URLSession.shared.data(for: request), let response = response as? HTTPURLResponse, response.statusCode == 200 { content = data }
            }
    }
    private var placeholder: some View { Label("Vista previa no disponible", systemImage: "photo").font(.caption).foregroundStyle(ArosTheme.secondary).frame(maxWidth: .infinity, maxHeight: .infinity) }
}
struct UniversityCalendarSourceView: View {
    @Environment(AppStore.self) private var store
    @State private var source: RemoteUniversityCalendar?
    @State private var error: String?
    @State private var busy = false
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Horario de Universidad").font(.headline)
            if let source {
                Text("\(source.group) · \(source.academicYear) · \(source.count) clases y exámenes importados").font(.subheadline)
                Text(source.lastSync.map { "Actualizado: \(displayDate($0))" } ?? "Primera importación pendiente").font(.caption).foregroundStyle(ArosTheme.secondary)
                if let detail = source.detail { Text(detail).font(.caption).foregroundStyle(ArosTheme.secondary) }
                if source.status == "paused" { Text("Calendario en pausa. Reanuda la conexión en Ajustes antes de actualizar.").font(.caption) }
                Button(busy ? "Actualizando…" : source.configured ? "Actualizar horario" : "Importar horario oficial") { Task { await synchronize() } }.disabled(busy || source.status == "paused")
                if let value = source.sourceUrl, let url = URL(string: value), url.scheme == "https" { Link("Ver fuente del horario", destination: url) }
            } else { Text("Consultando horario…").font(.caption) }
            if let issue = error ?? source?.error { Text(issue).font(.caption).foregroundStyle(.red) }
        }.task { await load() }
    }
    private func load() async { guard let client = store.apiClient else { return }; do { source = try await client.request("/calendar/university/status") } catch { self.error = error.localizedDescription } }
    private func synchronize() async { guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false }; do { let _: JSONValue = try await client.request("/calendar/university/sync", method: "POST", body: [:]); await load(); await store.refreshRemote() } catch { self.error = error.localizedDescription } }
}

struct PrismaOriginalActions: View {
    @Environment(AppStore.self) private var store
    let asset: Resource
    @State private var downloaded: URL?
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(busy ? "Descargando…" : "Descargar original") { Task {
                guard let client = store.apiClient else { return }; busy = true; error = nil; defer { busy = false }
                do { downloaded = try await client.download(asset) } catch { self.error = error.localizedDescription }
            } }.disabled(busy)
            if let downloaded { ShareLink(item: downloaded) { Label("Abrir o compartir original", systemImage: "square.and.arrow.up") } }
            if let error { Text(error).font(.caption).foregroundStyle(.red) }
        }
    }
}
