import Foundation

// Integration check against the local, disposable DEMO_MODE=true server.
// It refuses non-loopback destinations and never prints authentication tokens.
@main struct APIContractChecks {
    static func main() async throws {
        var client = try APIClient.make(url: "http://127.0.0.1:8000")
        let login: LoginResult = try await client.request("/auth/demo", method: "POST", body: [:])
        client.token = login.accessToken
        let user: RemoteUser = try await client.request("/me")
        precondition(user.demo, "Contract checks may only run in the local demo")
        let dashboard: Dashboard = try await client.request("/dashboard")
        let resources: [Resource] = try await client.request("/items?limit=1000")
        let inbox: [RemoteInbox] = try await client.request("/inbox")
        let connectors: [RemoteConnector] = try await client.request("/connectors")
        let automations: [RemoteAutomation] = try await client.request("/automations")
        let _: [RemoteRun] = try await client.request("/automations/runs/history")
        let _: [RemoteMessage] = try await client.request("/chat")
        let preferences: RemoteModelPreferences = try await client.request("/ai/preferences")
        precondition(!preferences.models.isEmpty && preferences.models.contains(where: { $0.id == preferences.model }), "Current model must be advertised by the server")
        let agents: RemoteAgentStatus = try await client.request("/agents")
        precondition(agents.agents.contains(where: { $0.id == "aros" }) && agents.model == preferences.model, "The general agent uses the server-configured model")
        let _: [RemoteAgentJob] = try await client.request("/agents/runs")
        let calendar: RemoteCalendarPlan = try await client.request("/calendar/day?day=2026-10-01&zone=Europe%2FMadrid")
        precondition(calendar.timezone == "Europe/Madrid")
        let health: RemoteHealth = try await client.request("/server")
        precondition(dashboard.summaries.count == 6 && !resources.isEmpty && !connectors.isEmpty && !automations.isEmpty && health.ramTotal > 0)
        let payload: [String: Any] = ["kind":"task","area":"personal","title":"Swift API contract test","description":"Disposable local test","status":"pending","priority":"normal","due_at":NSNull(),"data":[:]]
        let created: Resource = try await client.request("/items", method: "POST", body: payload)
        var draft = ResourceDraft(resource: created); draft.status = "done"
        let updated: Resource = try await client.request("/items/\(created.id)", method: "PATCH", body: try draft.body())
        precondition(updated.version == 2 && updated.status == "done")
        let confirmation = try await client.confirmation("item.delete", payload: ["id": created.id])
        let _: OKResult = try await client.request("/items/\(created.id)", method: "DELETE", confirmation: confirmation)
        let _: OKResult = try await client.request("/auth/logout", method: "POST", body: [:])
        print("PASS: native URLSession authentication, all shared DTOs, dashboard, \(inbox.count) Inbox entries, real metrics, create/update/version and confirmed deletion")
    }
}
