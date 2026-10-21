import XCTest
@testable import Orchestragent

final class StubProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (code, data) = try Self.handler!(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

@MainActor
final class ClientTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suite: String!
    private var api: APIClient!

    override func setUp() async throws {
        suite = "OrchestragentTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)!
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        api = APIClient(session: URLSession(configuration: configuration))
    }

    override func tearDown() async throws {
        defaults.removePersistentDomain(forName: suite)
        StubProtocol.handler = nil
    }

    func testLostResponsePreservesExactSubmissionAcrossRestartAndRetry() async throws {
        StubProtocol.handler = { _ in throw URLError(.networkConnectionLost) }
        let original = DispatcherModel(api: api, defaults: defaults)
        original.draft = "实现加法"
        original.scenario = "approval"
        await original.submit()
        let pending = try XCTUnwrap(original.pending)
        XCTAssertNotNil(original.error)
        let restored = DispatcherModel(api: api, defaults: defaults)
        XCTAssertEqual(restored.pending, pending)
        await restored.submit()
        XCTAssertEqual(restored.pending, pending)
        XCTAssertEqual(restored.draft, "实现加法")
    }

    func testDiscardPendingKeepsDraftWithoutSendingCancellation() async throws {
        StubProtocol.handler = { _ in throw URLError(.networkConnectionLost) }
        let model = DispatcherModel(api: api, defaults: defaults)
        model.draft = "保留我的草稿"
        await model.submit()
        XCTAssertNotNil(model.pending)
        model.discardPending()
        XCTAssertNil(model.pending)
        XCTAssertNil(defaults.data(forKey: "pendingSubmission"))
        XCTAssertEqual(model.draft, "保留我的草稿")
    }

    func testRequestUsesContractKeys() throws {
        let submission = Submission(requestID: "request-1", prompt: "hello", scenario: "happy")
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(submission)) as? [String: String])
        XCTAssertEqual(object, ["request_id": "request-1", "prompt": "hello", "scenario": "happy", "mode": "mock"])
        let decision = APIClient.Decision(decision: "approve", operationDigest: "displayed-digest")
        let approval = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(decision)) as? [String: String])
        XCTAssertEqual(approval, ["decision": "approve", "operation_digest": "displayed-digest"])
    }

    func testPollingReconcilesAcceptedRequestAfterResponseWasLost() async throws {
        StubProtocol.handler = { _ in throw URLError(.networkConnectionLost) }
        let model = DispatcherModel(api: api, defaults: defaults)
        model.draft = "实现加法"
        await model.submit()
        let pending = try XCTUnwrap(model.pending)
        let task = DispatchTask(id: "task-1", requestID: pending.requestID, prompt: pending.prompt,
                                scenario: pending.scenario, status: "completed", stage: "summarize",
                                createdAt: 100, updatedAt: 110, provider: "mock", sessionID: "session",
                                attempt: 1, nextRunAt: 0, result: "完成", error: "", revision: "rev",
                                costUSD: 0, steps: [], approval: nil, artifacts: [])
        let list = try JSONEncoder().encode(["tasks": [task]])
        StubProtocol.handler = { request in
            if request.url?.path == "/health" { return (200, Data(#"{"mode":"mock","version":2}"#.utf8)) }
            if request.url?.path == "/tasks" { return (200, list) }
            return (200, Data(#"{"events":[],"cursor":0}"#.utf8))
        }
        await model.refresh()
        XCTAssertNil(model.pending)
        XCTAssertNil(model.error)
        XCTAssertEqual(model.selectedTask?.id, task.id)
        XCTAssertTrue(model.connected)
        XCTAssertEqual(model.draft, "")
        XCTAssertNil(defaults.data(forKey: "pendingSubmission"))
        // A list request begun before creation may omit the local task; it is not a deletion.
        StubProtocol.handler = { request in
            if request.url?.path == "/health" { return (200, Data(#"{"mode":"mock","version":2}"#.utf8)) }
            if request.url?.path == "/tasks" { return (200, Data(#"{"tasks":[]}"#.utf8)) }
            return (200, Data(#"{"events":[],"cursor":0}"#.utf8))
        }
        await model.refresh()
        XCTAssertEqual(model.selectedTask?.id, task.id)
        XCTAssertEqual(model.tasks.count, 1)
    }

    func testServerErrorsAreVisible() async {
        StubProtocol.handler = { _ in (409, Data(#"{"error":"digest changed"}"#.utf8)) }
        do { _ = try await api.tasks(); XCTFail("Expected HTTP failure") }
        catch { XCTAssertTrue(error.localizedDescription.contains("digest changed")) }
    }

    func testLegacySubmissionAndDemoScenariosStayMock() throws {
        let old = Data(#"{"request_id":"legacy","prompt":"test","scenario":"happy"}"#.utf8)
        XCTAssertEqual(try JSONDecoder().decode(Submission.self, from: old).mode, "mock")
        XCTAssertEqual(Submission(prompt: "test", scenario: "approval", mode: "live").mode, "mock")
        XCTAssertEqual(Submission(prompt: "test", scenario: "happy", mode: "live").mode, "live")
    }

    func testHealthDefaultsNewRequestsToLiveButRespectsUserSelection() async {
        StubProtocol.handler = { request in
            if request.url?.path == "/health" { return (200, Data(#"{"mode":"live","version":2}"#.utf8)) }
            return (200, Data(#"{"tasks":[]}"#.utf8))
        }
        let model = DispatcherModel(api: api, defaults: defaults)
        await model.refresh()
        XCTAssertEqual(model.submissionMode, "live")
        model.scenario = "approval"
        XCTAssertEqual(model.submissionMode, "mock")
        model.scenario = "happy"
        model.chooseMode("mock")
        await model.refresh()
        XCTAssertEqual(model.submissionMode, "mock")
        XCTAssertEqual(model.serverMode, "live")
    }

    func testNullableLiveChargeAndLegacyTaskDecoding() throws {
        let old = #"{"id":"t","request_id":"r","prompt":"p","scenario":"happy","status":"queued","stage":"plan","created_at":1,"updated_at":1,"provider":"mock","session_id":"","attempt":1,"next_run_at":0,"result":"","error":"","revision":"","cost_usd":0,"steps":[],"approval":null,"artifacts":[]}"#
        let legacy = try JSONDecoder().decode(DispatchTask.self, from: Data(old.utf8))
        XCTAssertEqual(legacy.executionMode, "mock")
        XCTAssertEqual(legacy.costUSD, 0)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(old.utf8)) as? [String: Any])
        object["mode"] = "live"
        object["cost_usd"] = NSNull()
        let live = try JSONDecoder().decode(DispatchTask.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertEqual(live.executionMode, "live")
        XCTAssertNil(live.costUSD)
    }

    func testOversizedPromptDoesNotBecomeUnretryablePendingRequest() async {
        let model = DispatcherModel(api: api, defaults: defaults)
        model.draft = String(repeating: "字", count: 6000)
        await model.submit()
        XCTAssertNil(model.pending)
        XCTAssertNotNil(model.error)
    }

    func testForwardCompatibleEventsDecodeNestedData() throws {
        let json = #"{"events":[{"seq":7,"task_id":"task","kind":"progress","at":100,"extra":"future","data":{"count":2,"nested":{"ok":true},"items":["a",null]}}],"cursor":7,"future_field":true}"#
        let page = try JSONDecoder().decode(APIClient.EventList.self, from: Data(json.utf8))
        XCTAssertEqual(page.cursor, 7)
        XCTAssertEqual(page.events.first?.id, 7)
        XCTAssertTrue(page.events[0].detail.contains("nested: ok: true"))
    }

    func testStaleSettingsSavePreservesDraftAndShowsConflict() async throws {
        let initial = DispatcherSettings(revision: 4, mode: "mock", policy: .initial)
        let data = try JSONEncoder().encode(initial)
        StubProtocol.handler = { request in
            if request.httpMethod == "GET" { return (200, data) }
            return (409, Data(#"{"error":"settings changed; reload"}"#.utf8))
        }
        let model = SettingsModel(api: api)
        await model.load()
        model.policy.instructions = "检查边界条件"
        await model.save()
        XCTAssertEqual(model.saved?.revision, 4)
        XCTAssertEqual(model.policy.instructions, "检查边界条件")
        XCTAssertTrue(model.hasChanges)
        XCTAssertTrue(model.error?.contains("409") == true)
    }

    func testProposalRequiresExplicitApplyAndUsesPreviewIdentifier() async throws {
        var changed = DispatchPolicy.initial
        changed.instructions = "检查边界条件"
        let initial = try JSONEncoder().encode(DispatcherSettings(revision: 4, mode: "mock", policy: .initial))
        let proposal = SettingsProposal(id: "exact-preview", baseRevision: 4, mode: "mock", summary: "追加原句", policy: changed)
        let proposalData = try JSONEncoder().encode(proposal)
        let applied = try JSONEncoder().encode(DispatcherSettings(revision: 5, mode: "mock", policy: changed))
        StubProtocol.handler = { request in
            switch (request.httpMethod, request.url?.path) {
            case ("GET", "/settings"): return (200, initial)
            case ("POST", "/settings/proposals"): return (200, proposalData)
            case ("POST", "/settings/proposals/exact-preview/apply"): return (200, applied)
            default: throw URLError(.badURL)
            }
        }
        let model = SettingsModel(api: api)
        await model.load()
        model.instruction = "检查边界条件"
        await model.propose()
        XCTAssertEqual(model.saved?.revision, 4)
        XCTAssertEqual(model.policy.instructions, "")
        let shown = try XCTUnwrap(model.proposal)
        XCTAssertEqual(shown.policy.instructions, "检查边界条件")
        await model.apply(shown)
        XCTAssertEqual(model.saved?.revision, 5)
        XCTAssertEqual(model.policy, changed)
        XCTAssertNil(model.proposal)
        XCTAssertNil(model.error)
    }
}

final class LocalhostIntegrationTests: XCTestCase {
    private let api = APIClient(session: URLSession(configuration: .ephemeral))

    private func waitForTask(_ id: String, status: String) async throws -> DispatchTask {
        for _ in 0..<100 {
            let task: DispatchTask = try await api.request("/tasks/\(id)")
            if task.status == status { return task }
            if task.isTerminal { XCTFail("Unexpected terminal status \(task.status): \(task.error)"); return task }
            try await Task.sleep(for: .milliseconds(150))
        }
        throw NSError(domain: "LocalhostIntegration", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Local server did not reach \(status) within 15 seconds"])
    }

    func testLocalhostMockApproval() async throws {
        let initial: DispatcherSettings
        do { initial = try await api.request("/settings") }
        catch let error as URLError where [.cannotConnectToHost, .networkConnectionLost, .timedOut].contains(error.code) {
            throw XCTSkip("Optional localhost integration: start Mock server on port 8765 to run")
        }
        let created = try await api.submit(Submission(prompt: "iOS 本地集成验证：实现加法函数，审查并测试。", scenario: "approval", mode: "mock"))
        let waiting = try await waitForTask(created.id, status: "waiting_approval")
        let approval = try XCTUnwrap(waiting.approval)
        XCTAssertFalse(approval.operationDigest.isEmpty)
        _ = try await api.decide("approve", taskID: created.id, approval: approval)
        let completed = try await waitForTask(created.id, status: "completed")
        XCTAssertEqual(completed.status, "completed")
        XCTAssertEqual(completed.provider, "mock")
        XCTAssertEqual(completed.costUSD, 0)
        XCTAssertFalse(completed.artifacts.isEmpty)
        XCTAssertEqual(completed.policyRevision, initial.revision)
        XCTAssertEqual(completed.executionMode, "mock")
    }

    func testLocalhostMockSettingsOnly() async throws {
        let initial: DispatcherSettings
        do { initial = try await api.request("/settings") }
        catch let error as URLError where [.cannotConnectToHost, .networkConnectionLost, .timedOut].contains(error.code) {
            throw XCTSkip("Optional localhost integration: start Mock server on port 8765 to run")
        }
        let health: ServerHealth = try await api.request("/health")
        guard health.mode == "mock", initial.mode == "mock" else {
            throw XCTSkip("Settings proposals use real inference on live servers; tests never invoke it")
        }

        let sentence = "集成验证优先覆盖边界条件。"
        let proposal: SettingsProposal = try await api.request("/settings/proposals", method: "POST",
            body: JSONEncoder().encode(SettingsModel.Instruction(instruction: sentence)))
        XCTAssertEqual(proposal.baseRevision, initial.revision)
        XCTAssertTrue(proposal.policy.instructions.contains(sentence))
        let applied: DispatcherSettings = try await api.request("/settings/proposals/\(proposal.id)/apply", method: "POST",
            body: JSONEncoder().encode(SettingsModel.EmptyBody()))
        XCTAssertEqual(applied.policy, proposal.policy)
        XCTAssertEqual(applied.revision, initial.revision + 1)
        let restored: DispatcherSettings = try await api.request("/settings", method: "POST",
            body: JSONEncoder().encode(SettingsModel.Update(baseRevision: applied.revision, policy: initial.policy)))
        XCTAssertEqual(restored.policy, initial.policy)
    }
}
