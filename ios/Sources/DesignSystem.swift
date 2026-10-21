import SwiftUI

extension Area {
    var tint: Color {
        switch self {
        case .learning: return Color(red: 0.20, green: 0.43, blue: 0.98)
        case .events: return Color(red: 0.95, green: 0.23, blue: 0.35)
        case .technology: return Color(red: 0.59, green: 0.24, blue: 0.94)
        case .resources: return Color(red: 0.02, green: 0.65, blue: 0.46)
        case .server: return Color(red: 0.40, green: 0.45, blue: 0.54)
        case .personal: return Color(red: 0.95, green: 0.55, blue: 0.10)
        }
    }
}

enum ArosTheme {
    static let ink = Color(red: 0.08, green: 0.10, blue: 0.15)
    static let secondary = Color(red: 0.36, green: 0.40, blue: 0.48)
    static let blue = Color(red: 0.08, green: 0.40, blue: 0.98)
    static let canvas = Color(red: 0.965, green: 0.975, blue: 0.995)
    static let line = Color(red: 0.82, green: 0.85, blue: 0.91)
    static let contentRadius: CGFloat = 25
    static let horizontalInset: CGFloat = 20
}

struct ArosBackground: View {
    @Environment(\.accessibilityReduceTransparency) private var opaque
    var body: some View {
        ZStack {
            ArosTheme.canvas
            if !opaque {
                Ellipse().fill(Color.blue.opacity(0.09)).frame(width: 340, height: 450)
                    .blur(radius: 65).offset(x: 140, y: -290)
                Ellipse().fill(Color.purple.opacity(0.065)).frame(width: 310, height: 380)
                    .blur(radius: 65).offset(x: -160, y: 30)
                Ellipse().fill(Color.pink.opacity(0.045)).frame(width: 300, height: 300)
                    .blur(radius: 60).offset(x: 140, y: 340)
            }
        }.ignoresSafeArea().accessibilityHidden(true)
    }
}

struct GlassSurface: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var opaque
    @Environment(\.colorSchemeContrast) private var contrast
    var radius: CGFloat = 23
    func body(content: Content) -> some View {
        content
            .background {
                if opaque { RoundedRectangle(cornerRadius: radius).fill(.white) }
                else {
                    RoundedRectangle(cornerRadius: radius, style: .continuous).fill(.regularMaterial)
                    RoundedRectangle(cornerRadius: radius).fill(.white.opacity(0.62))
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: radius))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).stroke(contrast == .increased ? ArosTheme.line : .white.opacity(0.95), lineWidth: 1))
            .shadow(color: Color(red: 0.28, green: 0.32, blue: 0.53).opacity(0.045), radius: 15, y: 7)
    }
}


// Reading surfaces stay quiet and solid; translucent material belongs to chrome.
struct ReadingSurface: ViewModifier {
    @Environment(\.colorSchemeContrast) private var contrast
    var accent: Color
    func body(content: Content) -> some View {
        content
            .background {
                RoundedRectangle(cornerRadius: ArosTheme.contentRadius, style: .continuous)
                    .fill(.white)
                RoundedRectangle(cornerRadius: ArosTheme.contentRadius, style: .continuous)
                    .fill(LinearGradient(colors: [accent.opacity(0.045), .white.opacity(0)], startPoint: .topLeading, endPoint: .bottomTrailing))
            }
            .clipShape(RoundedRectangle(cornerRadius: ArosTheme.contentRadius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: ArosTheme.contentRadius, style: .continuous)
                .stroke(contrast == .increased ? ArosTheme.line : accent.opacity(0.09), lineWidth: 1))
            .shadow(color: ArosTheme.ink.opacity(0.035), radius: 14, y: 5)
    }
}

extension View {
    func readingSurface(accent: Color) -> some View { modifier(ReadingSurface(accent: accent)) }
    func glass(radius: CGFloat = 23) -> some View { modifier(GlassSurface(radius: radius)) }
    @ViewBuilder func inlineTitle() -> some View {
        #if os(iOS)
        self.navigationBarTitleDisplayMode(.inline).toolbar(.visible, for: .navigationBar)
        #else
        self
        #endif
    }
    @ViewBuilder func homeNavigation() -> some View {
        #if os(iOS)
        self.toolbar(.hidden, for: .navigationBar)
        #else
        self
        #endif
    }
}

struct AreaIcon: View {
    let area: Area
    var size: CGFloat = 34
    var body: some View {
        Image(systemName: area.symbol)
            .font(.system(size: size * 0.52, weight: .medium))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(LinearGradient(colors: [area.tint.opacity(0.65), area.tint], startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: size * 0.3))
            .overlay(RoundedRectangle(cornerRadius: size * 0.3).stroke(.white.opacity(0.45)))
            .shadow(color: area.tint.opacity(0.16), radius: 7, y: 4)
            .accessibilityHidden(true)
    }
}

struct ArosOrb: View {
    var size: CGFloat = 29
    var body: some View {
        ZStack {
            Circle().fill(Color(red: 0.48, green: 0.74, blue: 1))
            Circle().fill(AngularGradient(colors: [.cyan, .blue.opacity(0.8), .purple, .pink.opacity(0.8), .cyan], center: .center))
            Circle().fill(RadialGradient(colors: [.white.opacity(0.8), .clear], center: .topLeading, startRadius: 0, endRadius: size * 0.7))
            Circle().fill(.white.opacity(0.15)).blur(radius: size * 0.15).padding(size * 0.2)
        }.frame(width: size, height: size).clipShape(Circle()).accessibilityHidden(true)
    }
}

struct SummaryLine: View {
    let row: SummaryRow
    let tint: Color
    var compact = false
    var body: some View {
        HStack(alignment: .top, spacing: compact ? 7 : 12) {
            Image(systemName: row.symbol).font(.system(size: compact ? 12 : 17))
                .foregroundStyle(tint).frame(width: compact ? 19 : 32, height: compact ? 21 : 36)
                .background(tint.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(row.title).font(compact ? .caption : .subheadline).fontWeight(.medium)
                    .foregroundStyle(ArosTheme.ink).fixedSize(horizontal: false, vertical: true)
                if !compact {
                    Text(row.subtitle).font(.caption).foregroundStyle(ArosTheme.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }.accessibilityElement(children: .combine)
    }
}

struct Sparkline: Shape {
    let values: [Double]
    func path(in rect: CGRect) -> Path {
        var path = Path()
        guard values.count > 1 else { return path }
        let points = values.enumerated().map { index, value in
            CGPoint(x: rect.width * Double(index) / Double(values.count - 1), y: rect.height * (1 - value))
        }
        path.move(to: points[0])
        for i in 1..<points.count {
            let midpoint = (points[i - 1].x + points[i].x) / 2
            path.addCurve(to: points[i], control1: CGPoint(x: midpoint, y: points[i - 1].y), control2: CGPoint(x: midpoint, y: points[i].y))
        }
        return path
    }
}

struct MetricRow: View {
    let metric: ServerMetric
    let tint: Color
    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(metric.title).font(.caption2).foregroundStyle(ArosTheme.secondary)
                Text(metric.value).font(.subheadline.weight(.semibold)).foregroundStyle(ArosTheme.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            Sparkline(values: metric.samples).stroke(tint, style: StrokeStyle(lineWidth: 1.4, lineCap: .round))
                .frame(width: 66, height: 26).accessibilityHidden(true)
        }.accessibilityElement(children: .combine)
    }
}

struct DemoLabel: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        Label(store.isDemo ? "Datos de demostración" : "Conectado a tu espacio", systemImage: store.isDemo ? "flask" : "checkmark.icloud")
            .font(.caption2).foregroundStyle(ArosTheme.secondary)
    }
}

struct StateNotice: View {
    let state: LoadState
    let retry: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(state == .offline ? "Sin conexión" : "Datos sin actualizar",
                  systemImage: state == .offline ? "wifi.slash" : "clock.arrow.circlepath")
                .font(.subheadline.weight(.semibold))
            Text("Estás viendo la última copia disponible. Puedes volver a cargarla.")
                .font(.caption).foregroundStyle(ArosTheme.secondary)
            Button("Volver a cargar", action: retry).font(.subheadline.weight(.semibold)).frame(minHeight: 44)
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading).glass()
    }
}

extension View {
    @ViewBuilder func accountInput(url: Bool = false) -> some View {
        #if os(iOS)
        self.keyboardType(url ? .URL : .emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
        #else
        self
        #endif
    }
}
