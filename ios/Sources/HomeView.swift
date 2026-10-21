import SwiftUI

struct HomeView: View {
    @Environment(AppStore.self) private var store
    var date = Date.now
    var body: some View {
        GeometryReader { geometry in
            ScrollView { HomeContent(availableWidth: geometry.size.width, date: date) }
                .refreshable { await store.refresh() }
        }.homeNavigation()
    }
}

struct HomeContent: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var textSize
    @ScaledMetric(relativeTo: .largeTitle) private var nameSize: CGFloat = 49
    @State private var weatherDetails: RemoteWeather?
    var availableWidth: CGFloat
    var date = Date.now

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            header
            DemoLabel().padding(.top, -4)
            switch store.state {
            case .loading:
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Preparando tu día…").font(.subheadline).foregroundStyle(ArosTheme.secondary)
                }.frame(maxWidth: .infinity, minHeight: 220).glass()
            case .empty:
                ContentUnavailableView {
                    Label("Tu espacio está listo", systemImage: "sun.max")
                } description: {
                    Text("Todavía no hay fuentes conectadas. Explora la interfaz con datos de ejemplo.")
                } actions: {
                    Button("Cargar demostración") { Task { await store.refresh() } }.buttonStyle(.borderedProminent)
                }
            default:
                if store.state == .offline || store.state == .stale {
                    StateNotice(state: store.state) { Task { await store.refresh() } }
                }
                contextStrip
                LazyVGrid(columns: columns(width: availableWidth), alignment: .leading, spacing: 16) {
                    ForEach(store.dashboard.summaries) { summary in
                        NavigationLink(value: Destination.area(summary.area)) {
                            AreaCard(summary: summary, metrics: store.dashboard.metrics)
                        }.buttonStyle(CardButtonStyle())
                    }
                }
            }
            Text("Solo lo que importa, en un mismo lugar.")
                .font(.caption).foregroundStyle(ArosTheme.secondary).frame(maxWidth: .infinity).padding(.vertical, 8)
        }.padding(.horizontal, ArosTheme.horizontalInset).padding(.top, 18).padding(.bottom, 20)
        .task(id: store.connected) { if let client = store.apiClient { weatherDetails = try? await client.request("/weather") } else { weatherDetails = nil } }
    }

    private func columns(width: CGFloat) -> [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: 16, alignment: .top),
              count: textSize >= .xxLarge || width < 370 ? 1 : 2)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 16) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: 16) { dateLabel; Spacer(minLength: 12); headerActions }
                VStack(alignment: .leading, spacing: 12) { dateLabel; headerActions.frame(maxWidth: .infinity, alignment: .trailing) }
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(DayGreeting.text(at: date)).font(.largeTitle.weight(.semibold)).tracking(-1)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Aros.").font(.system(size: nameSize, weight: .bold)).tracking(-1.8)
                    .fixedSize(horizontal: false, vertical: true).accessibilityLabel("Aros")
            }.accessibilityElement(children: .combine)
        }.foregroundStyle(ArosTheme.ink).buttonStyle(.plain)
    }

    private var dateLabel: some View {
        Text(date.formatted(.dateTime.weekday(.wide).day().month(.wide).locale(Locale(identifier: "es_ES"))))
            .font(.subheadline).foregroundStyle(ArosTheme.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var headerActions: some View {
        HStack(spacing: 10) {
            Button { store.open(.search) } label: {
                Image(systemName: "magnifyingglass").font(.title3)
                    .frame(width: 44, height: 44).glass(radius: 22)
            }.accessibilityLabel("Buscar en Aros")
            Button { store.open(.settings) } label: {
                Text("E").font(.headline).foregroundStyle(ArosTheme.secondary)
                    .frame(width: 44, height: 44)
                    .background(.white, in: Circle())
                    .overlay(Circle().stroke(ArosTheme.line.opacity(0.65), lineWidth: 1))
            }.accessibilityLabel("Perfil y ajustes")
        }
    }

    private var contextStrip: some View {
        Group {
            if textSize >= .xxLarge || availableWidth < 370 {
                VStack(spacing: 0) { weatherButton; Divider().padding(.horizontal, 14); attentionButton }
            } else {
                HStack(spacing: 0) { weatherButton; Divider().frame(height: 36); attentionButton }
            }
        }.glass()
    }

    private var weatherButton: some View {
        Button { store.open(.weather) } label: {
            HStack(spacing: 11) {
                Image(systemName: store.dashboard.weatherSymbol).symbolRenderingMode(.multicolor).font(.system(size: 29)).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(weatherDetails?.location?.label ?? store.dashboard.city).font(.subheadline.weight(.semibold)).foregroundStyle(ArosTheme.ink)
                    if store.connected, let forecast = weatherDetails?.forecast { Text("\(Int(forecast.temperature.rounded())) °C · \(forecast.description)").font(.caption).foregroundStyle(ArosTheme.secondary) } else { Text("\(store.dashboard.temperature) · \(store.dashboard.weather)").font(.caption).foregroundStyle(ArosTheme.secondary) }
                    if store.connected, let weather = weatherDetails, let forecast = weather.forecast {
                        Text(forecast.compact).font(.caption2).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
                        Text(weather.stale ? "Previsión sin actualizar" : forecast.advice).font(.caption2).foregroundStyle(ArosTheme.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
            }.padding(14).frame(maxWidth: .infinity, minHeight: 74)
        }.buttonStyle(.plain).accessibilityElement(children: .combine).accessibilityHint("Ver detalle del clima")
    }

    private var attentionButton: some View {
        Button { store.open(.inbox) } label: {
            HStack(spacing: 10) {
                Image(systemName: "tray.fill").font(.subheadline).foregroundStyle(.white)
                    .frame(width: 29, height: 29).background(Area.events.tint.gradient, in: RoundedRectangle(cornerRadius: 9)).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(store.dashboard.unresolved.isEmpty ? "Todo al día" : "\(store.dashboard.unresolved.count) cosas")
                        .font(.subheadline.weight(.semibold)).foregroundStyle(ArosTheme.ink)
                    Text(store.dashboard.unresolved.isEmpty ? "Nada pendiente" : "requieren tu atención")
                        .font(.caption).foregroundStyle(ArosTheme.secondary)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(ArosTheme.secondary).accessibilityHidden(true)
            }.padding(14).frame(maxWidth: .infinity, minHeight: 74)
        }.buttonStyle(.plain)
    }
}

struct AreaCard: View {
    @Environment(AppStore.self) private var store
    let summary: AreaSummary
    let metrics: [ServerMetric]
    @Environment(\.dynamicTypeSize) private var textSize
    @Environment(\.accessibilityReduceTransparency) private var opaque

    var body: some View {
        VStack(alignment: .leading, spacing: 17) {
            HStack(spacing: 9) {
                AreaIcon(area: summary.area)
                Text(summary.area.title).font(.subheadline.weight(.semibold)).tracking(-0.4)
                    .foregroundStyle(ArosTheme.ink).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.caption.weight(.medium)).foregroundStyle(ArosTheme.secondary).accessibilityHidden(true)
            }
            if summary.area == .server {
                Label(store.connected ? (store.remoteHealth == nil ? "Sin conexión" : store.state == .offline ? "Última lectura" : "Online") : "Online · ejemplo", systemImage: "circle.fill").font(.caption2.weight(.medium))
                    .foregroundStyle(Color(red: 0.02, green: 0.48, blue: 0.26))
                ForEach(Array(metrics.enumerated()), id: \.element.id) { index, metric in
                    MetricRow(metric: metric, tint: [.cyan, .purple, .orange][index % 3])
                    if index < metrics.count - 1 { Divider().opacity(0.4) }
                }
            } else if summary.area == .events && store.connected && !store.resources.contains(where: {
                $0.area == .events && $0.kind == "event" && $0.status != "archived" && (LearningResourceOrder.date($0.dueAt) ?? .distantPast) > Date()
            }) {
                ForEach(EventsHomeSummary.rows(store.resources)) { row in
                    SummaryLine(row: row, tint: summary.area.tint, compact: false)
                }
            } else {
                if let headline = summary.headline {
                    VStack(alignment: .leading, spacing: 3) {
                        if let eyebrow = summary.eyebrow { Text(eyebrow).font(.caption).foregroundStyle(ArosTheme.secondary) }
                        Text(headline).font(.subheadline.weight(.semibold)).foregroundStyle(ArosTheme.ink)
                        if let detail = summary.detail { Text(store.connected ? displayDate(detail) : detail).font(.caption).foregroundStyle(ArosTheme.secondary) }
                        if let footnote = summary.footnote { Text(footnote).font(.caption2).foregroundStyle(ArosTheme.secondary) }
                    }.fixedSize(horizontal: false, vertical: true)
                }
                ForEach(summary.rows) { row in
                    SummaryLine(row: row, tint: row.id == "material" ? .green : summary.area.tint,
                                compact: summary.headline != nil)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(18).frame(maxWidth: .infinity, minHeight: textSize >= .xxLarge ? 0 : 224, alignment: .topLeading)
        .background(alignment: .bottomTrailing) {
            if !opaque {
                CardDecoration(area: summary.area).opacity(summary.area == .personal || summary.area == .server ? 0 : 0.14)
                    .frame(width: 130, height: 140).offset(x: 28, y: 18).accessibilityHidden(true)
            }
        }
        .readingSurface(accent: summary.area.tint)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Abre \(summary.area.title)")
    }
}

struct CardDecoration: View {
    let area: Area
    var body: some View {
        ZStack {
            if area == .learning {
                Image(systemName: "book.closed.fill").font(.system(size: 112)).foregroundStyle(area.tint)
            } else if area == .events {
                RoundedRectangle(cornerRadius: 20).fill(LinearGradient(colors: [.pink.opacity(0.6), area.tint], startPoint: .top, endPoint: .bottom)).rotationEffect(.degrees(-12))
                ForEach(0..<3) { index in
                    Capsule().fill(.white.opacity(0.4)).frame(width: 13, height: 95).rotationEffect(.degrees(Double(index * 22 - 20))).offset(x: CGFloat(index * 35 - 35), y: -10)
                }
                ForEach(0..<5) { index in
                    Circle().fill(area.tint).frame(width: 27, height: 27).offset(x: CGFloat(index * 28 - 55), y: CGFloat(index % 2 == 0 ? 63 : 50))
                }
            } else if area == .resources {
                Image(systemName: "folder.fill").font(.system(size: 112)).foregroundStyle(area.tint)
            } else if area == .technology {
                ArosOrb(size: 122)
            }
        }
    }
}
struct CardButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.86 : 1)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
            .animation(reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 1), value: configuration.isPressed)
    }
}

struct AssistantDock: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        HStack(spacing: 3) {
            Button { store.openChat() } label: {
                HStack(spacing: 10) {
                    ArosOrb()
                    Text("Pregunta a Aros…").font(.subheadline).foregroundStyle(ArosTheme.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }.frame(minHeight: 44)
            }.buttonStyle(.plain).accessibilityLabel(store.isDemo ? "Abrir el asistente de demostración" : "Abrir el asistente de Aros")
            Button { store.openChat(.dictate) } label: {
                Image(systemName: "mic").frame(width: 44, height: 44)
            }.accessibilityLabel("Dictar al asistente")
            Button { store.openChat(.attach) } label: {
                Image(systemName: "paperclip").frame(width: 44, height: 44)
            }.accessibilityLabel("Adjuntar un archivo")
        }.buttonStyle(.plain).foregroundStyle(ArosTheme.secondary).padding(.horizontal, 13).padding(.vertical, 8).glass(radius: 27)
    }
}

struct BottomNavigation: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var textSize
    var body: some View {
        Group {
            if textSize.isAccessibilitySize {
                ScrollView(.horizontal) { navigationItems }.scrollIndicators(.hidden)
            } else { navigationItems }
        }.padding(5).glass(radius: 29)
    }

    private var navigationItems: some View {
        HStack(alignment: .top, spacing: 3) {
            ForEach(MainTab.allCases) { tab in
                Button { store.select(tab) } label: {
                    VStack(spacing: 5) {
                        Image(systemName: tab.symbol)
                            .font(.system(size: 20, weight: store.selectedTab == tab ? .semibold : .regular))
                            .accessibilityHidden(true)
                        Text(tab.title).font(.caption2.weight(store.selectedTab == tab ? .semibold : .regular))
                            .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(minWidth: textSize.isAccessibilitySize ? 116 : 0, maxWidth: textSize.isAccessibilitySize ? 116 : .infinity, minHeight: 52)
                    .padding(.vertical, 5)
                    .foregroundStyle(store.selectedTab == tab ? ArosTheme.blue : ArosTheme.secondary)
                    .background(store.selectedTab == tab ? .white : .clear, in: RoundedRectangle(cornerRadius: 23, style: .continuous))
                }.buttonStyle(.plain).accessibilityLabel(tab.title)
                    .accessibilityAddTraits(store.selectedTab == tab ? .isSelected : [])
            }
        }
    }
}
