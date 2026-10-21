import SwiftUI

struct ProviderPreference: Codable, Equatable, Identifiable {
    let id: String
    var model: String
    var instructions: String
    var name: String {
        id.isEmpty ? "未命名端点" : String(id.prefix(1)).uppercased() + String(id.dropFirst())
    }
}

struct DispatchPolicy: Codable, Equatable {
    var schedulerProvider: String
    var schedulerModel: String
    var instructions: String
    var providers: [ProviderPreference]
    enum CodingKeys: String, CodingKey {
        case instructions, providers
        case schedulerProvider = "scheduler_provider", schedulerModel = "scheduler_model"
    }
    static let initial = DispatchPolicy(schedulerProvider: "", schedulerModel: "", instructions: "",
                                       providers: [])
}

struct DispatcherSettings: Codable {
    let revision: Int
    let mode: String
    let policy: DispatchPolicy
}

struct SettingsProposal: Codable, Identifiable {
    let id: String
    let baseRevision: Int
    let mode: String
    let summary: String
    let policy: DispatchPolicy
    enum CodingKeys: String, CodingKey {
        case id, mode, summary, policy
        case baseRevision = "base_revision"
    }
}

@MainActor
final class SettingsModel: ObservableObject {
    @Published var policy = DispatchPolicy.initial
    @Published var instruction = ""
    @Published private(set) var saved: DispatcherSettings?
    @Published var proposal: SettingsProposal?
    @Published private(set) var busy = false
    @Published private(set) var error: String?
    @Published private(set) var message: String?
    @Published private(set) var providers: [String: ProviderReadiness] = [:]
    private let api: APIClient
    var hasChanges: Bool { saved.map { $0.policy != policy } ?? false }
    var isLive: Bool { saved?.mode == "live" }

    init(api: APIClient = APIClient()) { self.api = api }

    struct Update: Encodable {
        let baseRevision: Int
        let policy: DispatchPolicy
        enum CodingKeys: String, CodingKey { case policy; case baseRevision = "base_revision" }
    }
    struct Instruction: Encodable { let instruction: String }
    struct EmptyBody: Encodable {}

    private func accept(_ settings: DispatcherSettings) {
        saved = settings
        policy = settings.policy
        proposal = nil
    }

    func load() async {
        guard !busy else { return }
        busy = true
        error = nil
        message = nil
        defer { busy = false }
        do {
            accept(try await api.request("/settings"))
            providers = (try? await api.request("/providers")) ?? [:]
        }
        catch { self.error = error.localizedDescription }
    }

    func save() async {
        guard !busy, let saved else { return }
        busy = true
        error = nil
        message = nil
        defer { busy = false }
        do {
            let settings: DispatcherSettings = try await api.request("/settings", method: "POST",
                body: JSONEncoder().encode(Update(baseRevision: saved.revision, policy: policy)))
            accept(settings)
            message = "已保存为版本 \(settings.revision)，将在新任务中使用。"
        } catch { self.error = "保存未确认。\(error.localizedDescription)" }
    }

    func propose() async {
        guard !busy, !hasChanges, !instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        busy = true
        error = nil
        message = nil
        defer { busy = false }
        do {
            proposal = try await api.request("/settings/proposals", method: "POST",
                                            body: JSONEncoder().encode(Instruction(instruction: instruction)), timeout: 200)
        } catch { self.error = error.localizedDescription }
    }

    func apply(_ shownProposal: SettingsProposal) async {
        guard !busy else { return }
        busy = true
        error = nil
        message = nil
        defer { busy = false }
        do {
            let settings: DispatcherSettings = try await api.request("/settings/proposals/\(shownProposal.id)/apply", method: "POST",
                                                                    body: JSONEncoder().encode(EmptyBody()))
            accept(settings)
            instruction = ""
            message = "已应用预览，当前版本 \(settings.revision)。"
        } catch { self.error = "预览未应用。\(error.localizedDescription)" }
    }
}

struct SettingsView: View {
    @StateObject private var model = SettingsModel()
    @Environment(\.dismiss) private var dismiss
    @State private var discardShown = false
    @State private var reloadShown = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label(model.isLive ? "REAL · 调度配置" : "MOCK · 配置偏好", systemImage: "slider.horizontal.3").font(.headline)
                    Text(model.isLive ? "用于新任务的模型与执行要求。" : "仅保存偏好，真实执行未启用。")
                        .font(.caption).foregroundStyle(.secondary)
                    if let saved = model.saved { Text("当前设置版本 \(saved.revision)").font(.caption.monospaced()) }
                }
                if model.saved != nil {
                    Section {
                        TextField("例如：Claude 审查时优先检查边界条件", text: $model.instruction, axis: .vertical).lineLimit(2...5)
                        Button("生成配置预览", systemImage: "text.badge.plus") { Task { await model.propose() } }
                            .disabled(model.busy || model.hasChanges || model.instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    } header: { Text("一句话调整") } footer: {
                        Text(model.hasChanges ? "请先保存手动修改。" : (model.isLive ? "调用调度模型解析，预览后应用。" : "Mock 仅追加原句；查看预览后应用。"))
                    }
                    Section {
                        Picker("调度供应商", selection: $model.policy.schedulerProvider) {
                            ForEach(model.policy.providers) { provider in Text(provider.name).tag(provider.id) }
                        }
                        TextField("调度模型", text: $model.policy.schedulerModel)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                        TextField("全局要求", text: $model.policy.instructions, axis: .vertical).lineLimit(3...9)
                    } header: { Text("调度器") } footer: {
                        Text(model.isLive ? "按端点权限与预算执行；费用可能未知。" : "模型名称仅记录偏好。")
                    }
                    Section("供应商偏好") {
                        ForEach($model.policy.providers) { $provider in
                            DisclosureGroup(provider.name) {
                                if let state = model.providers[provider.id] {
                                    Label(state.ready ? "已就绪" : "未就绪", systemImage: state.ready ? "checkmark.circle" : "exclamationmark.circle")
                                        .font(.caption).foregroundStyle(state.ready ? .blue : .orange)
                                    if let message = state.message { Text(message).font(.caption).foregroundStyle(.secondary) }
                                    else if let auth = state.auth { Text(auth).font(.caption).foregroundStyle(.secondary) }
                                }
                                TextField("模型名称（可留空）", text: $provider.model)
                                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                                TextField("对该供应商的要求", text: $provider.instructions, axis: .vertical).lineLimit(3...8)
                            }
                        }
                    }
                    Section {
                        Button("保存手动配置") { Task { await model.save() } }
                            .disabled(model.busy || !model.hasChanges)
                        Button("重新读取已保存配置") {
                            if model.hasChanges { reloadShown = true }
                            else { Task { await model.load() } }
                        }.disabled(model.busy)
                    } footer: { Text("新任务会固定保存当前设置版本，已有任务保持其创建时的配置。") }
                } else {
                    Section {
                        if model.busy { ProgressView("读取配置…") }
                        else { Button("重新连接并读取配置") { Task { await model.load() } } }
                    }
                }
                if let error = model.error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
                }
                if model.busy && model.saved != nil { Section { ProgressView("处理中…") } }
                if let message = model.message {
                    Section { Label(message, systemImage: "checkmark.circle").font(.caption).foregroundStyle(.blue) }
                }
            }
            .disabled(model.busy && model.saved != nil)
            .navigationTitle("调度设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") {
                        if model.hasChanges { discardShown = true }
                        else { dismiss() }
                    }.disabled(model.busy)
                }
            }
            .interactiveDismissDisabled(model.hasChanges || model.busy)
            .confirmationDialog("放弃尚未保存的修改？", isPresented: $discardShown, titleVisibility: .visible) {
                Button("放弃修改并关闭", role: .destructive) { dismiss() }
            }
            .confirmationDialog("重新读取会放弃手动修改", isPresented: $reloadShown, titleVisibility: .visible) {
                Button("重新读取", role: .destructive) { Task { await model.load() } }
            }
            .sheet(item: $model.proposal) { proposal in proposalPreview(proposal) }
            .task { await model.load() }
        }.tint(.blue)
    }

    private func proposalPreview(_ proposal: SettingsProposal) -> some View {
        NavigationStack {
            Form {
                Section {
                    Text(proposal.mode == "live" ? "REAL · 配置预览" : "MOCK · 确定性追加预览").font(.headline)
                    Text(proposal.mode == "live" ? "由调度模型解析，确认后应用。" : "Mock 追加原句，未进行 AI 语义解析。")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(proposal.summary).font(.subheadline)
                    Text("基于版本 \(proposal.baseRevision)").font(.caption.monospaced())
                }
                Section("调度器与全局要求") {
                    LabeledContent("供应商", value: proposal.policy.schedulerProvider)
                    LabeledContent("模型", value: proposal.policy.schedulerModel)
                    Text(proposal.policy.instructions.isEmpty ? "（全局要求为空）" : proposal.policy.instructions).textSelection(.enabled)
                }
                ForEach(proposal.policy.providers) { provider in
                    Section(provider.name) {
                        LabeledContent("模型", value: provider.model.isEmpty ? "（未指定）" : provider.model)
                        Text(provider.instructions.isEmpty ? "（要求为空）" : provider.instructions).textSelection(.enabled)
                    }
                }
                if let error = model.error {
                    Section { Text(error).font(.caption).foregroundStyle(.orange) }
                }
                Section {
                    Button("确认并应用这份预览") { Task { await model.apply(proposal) } }
                        .disabled(model.busy)
                    Text("预览编号：\(proposal.id)").font(.caption2.monospaced()).textSelection(.enabled)
                }
            }
            .navigationTitle("配置预览").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("返回修改") { model.proposal = nil }.disabled(model.busy) } }
            .interactiveDismissDisabled(model.busy)
        }
    }
}
