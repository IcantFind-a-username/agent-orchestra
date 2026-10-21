import Foundation

struct APIClient {
    let baseURL: URL
    let session: URLSession

    init(baseURL: URL = URL(string: "http://localhost:8765")!, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    struct TaskList: Decodable { let tasks: [DispatchTask] }
    struct EventList: Decodable { let events: [TaskEvent]; let cursor: Int }
    struct ServerError: Decodable { let error: String }
    struct Action: Encodable { let action: String }
    struct Decision: Encodable {
        let decision: String
        let operationDigest: String
        enum CodingKeys: String, CodingKey { case decision; case operationDigest = "operation_digest" }
    }

    enum Failure: LocalizedError {
        case invalidResponse, server(Int, String), tooLarge
        var errorDescription: String? {
            switch self {
            case .invalidResponse: return "调度器返回了无法识别的响应。"
            case .server(let code, let message): return "\(message)（HTTP \(code)）"
            case .tooLarge: return "请求超过 16 KiB，请缩短任务描述。"
            }
        }
    }

    func request<T: Decodable>(_ path: String, method: String = "GET", body: Data? = nil, timeout: TimeInterval = 10) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw Failure.invalidResponse }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeout
        if let body {
            guard body.count <= 16 * 1024 else { throw Failure.tooLarge }
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw Failure.invalidResponse }
        guard (200..<300).contains(response.statusCode) else {
            let error = try? JSONDecoder().decode(ServerError.self, from: data)
            throw Failure.server(response.statusCode, error?.error ?? "请求未成功，请重试。")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func tasks() async throws -> [DispatchTask] {
        let response: TaskList = try await request("/tasks")
        return response.tasks
    }
    func submit(_ submission: Submission) async throws -> DispatchTask {
        try await request("/tasks", method: "POST", body: JSONEncoder().encode(submission))
    }
    func events(taskID: String, after: Int) async throws -> EventList {
        try await request("/tasks/\(taskID)/events?after=\(after)")
    }
    func action(_ action: String, taskID: String) async throws -> DispatchTask {
        try await request("/tasks/\(taskID)/actions", method: "POST", body: JSONEncoder().encode(Action(action: action)))
    }
    func decide(_ decision: String, taskID: String, approval: Approval) async throws -> DispatchTask {
        try await request("/tasks/\(taskID)/approvals/\(approval.id)", method: "POST",
                          body: JSONEncoder().encode(Decision(decision: decision, operationDigest: approval.operationDigest)))
    }
}
