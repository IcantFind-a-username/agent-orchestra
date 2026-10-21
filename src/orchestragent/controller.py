"""Deterministic, durable control plane; no model may authorize actions."""

import hashlib
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import replace

from orchestragent.adapters import MockAdapter, StepContext, StepResult
from orchestragent.contracts import STAGE_SCHEMAS
from orchestragent.models import (
    RESUMABLE,
    SCENARIOS,
    STAGES,
    TERMINAL,
    Approval,
    Artifact,
    Step,
    Task,
)
from orchestragent.registry import ProviderRegistry
from orchestragent.sandbox import Sandbox, detect
from orchestragent.serialization import dumps
from orchestragent.settings import SettingsStore
from orchestragent.store import Conflict, Store
from orchestragent.workspace import Workspace


class Controller:
    """Owns every state transition. Adapters return data; only this class writes."""

    def __init__(
        self,
        store: Store,
        workspace: Workspace,
        registry: ProviderRegistry | None = None,
        clock: Callable[[], float] = time.time,
        lease_seconds: float = 30,
        allow_bundled_tests: bool = False,
        sandbox: Sandbox | None = None,
        routes: dict[str, str] | None = None,
    ):
        self.store = store
        self.workspace = workspace
        self.registry = registry if registry is not None else ProviderRegistry([MockAdapter()])
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.allow_bundled_tests = allow_bundled_tests
        self.sandbox: Sandbox = sandbox if sandbox is not None else detect()
        # Which adapter handles each stage. Implement and review must not share one
        # when more than one provider is registered: that is the whole point of the split.
        self.routes = {stage: "mock" for stage in STAGES}
        if routes:
            self.routes.update(routes)
        if (
            len(self.registry.ids()) > 1
            and self.routes["implement"] == self.routes["review"]
        ):
            raise ValueError("Implementation and review must use different providers")
        for stage, provider in self.routes.items():
            if provider not in self.registry.ids():
                raise ValueError(f"Route for {stage} names an unregistered provider")
        self.settings = SettingsStore(store, self.registry.ids())

    def submit(
        self, request_id: str, prompt: str, scenario: str = "happy", mode: str = "mock"
    ) -> tuple[Task, bool]:
        if mode != "mock":
            raise ValueError("Only mock execution is available; no real adapter is registered")
        if scenario != "happy":
            mode = "mock"
        if not request_id or len(request_id) > 128:
            raise ValueError("request_id must contain 1–128 characters")
        if not prompt.strip() or len(prompt) > 8000:
            raise ValueError("prompt must contain 1–8000 characters")
        if scenario not in SCENARIOS:
            raise ValueError("Unknown Mock scenario")
        with self.store.transaction() as db:
            row = db.execute("SELECT body FROM tasks WHERE request_id=?", (request_id,)).fetchone()
            if row:
                task = self.store.decode(row["body"])
                if (task.prompt, task.scenario, task.mode) != (prompt, scenario, mode):
                    raise Conflict("request_id was already used for a different request")
                return task, False
            now = self.clock()
            task = Task(uuid.uuid4().hex, request_id, prompt, scenario, now, now)
            settings = self.settings.snapshot(db)
            policy_revision = settings["revision"]
            policy = settings["policy"]
            assert isinstance(policy_revision, int) and isinstance(policy, dict)
            task.policy_revision = policy_revision
            task.policy = policy
            task.mode = mode
            task.provider = "mock"
            task.billing = "mock"
            task.cost_usd = 0.0
            task.session_id = f"mock-implement-{task.id}"
            task.steps = [
                Step(
                    stage,
                    stage,
                    [STAGES[i - 1]] if i else [],
                    provider="mock",
                )
                for i, stage in enumerate(STAGES)
            ]
            self.store.save(db, task)
            self.store.event(
                db,
                task,
                "submitted",
                {
                    "provider": task.provider,
                    "cost_usd": task.cost_usd,
                    "mode": mode,
                    "policy_revision": policy_revision,
                },
            )
            return task, True

    def _record(
        self, db: sqlite3.Connection, task: Task, kind: str, data: dict[str, object] | None = None
    ) -> None:
        task.updated_at = self.clock()
        self.store.save(db, task)
        self.store.event(db, task, kind, data or {"status": task.status, "stage": task.stage})

    def recover(self) -> int:
        """Only called by the exclusive server owner after restart. Mock is replay-safe."""
        recovered = 0
        with self.store.transaction() as db:
            for task in self.store.all(db):
                if task.status == "running":
                    task.status = "queued"
                    task.error = ""
                    task.run_token = ""
                    task.lease_until = 0
                    task.steps[STAGES.index(task.stage)].status = "pending"
                    self._record(db, task, "recovered", {"reason": "Replay after restart"})
                    recovered += 1
        return recovered

    def expire_leases(self) -> int:
        expired = 0
        with self.store.transaction() as db:
            for task in self.store.all(db):
                if task.status == "running" and task.lease_until <= self.clock():
                    task.status = "timed_out"
                    task.error = "Step deadline exceeded; explicit resume required"
                    task.run_token = ""
                    task.steps[STAGES.index(task.stage)].status = "timed_out"
                    self._record(db, task, "timed_out")
                    expired += 1
        return expired

    def claim(self) -> Task | None:
        self.expire_leases()
        with self.store.transaction() as db:
            for task in reversed(self.store.all(db)):
                if task.status not in {"queued", "rate_limited"}:
                    continue
                if task.next_run_at > self.clock():
                    continue
                if any(
                    step.status != "completed" for step in task.steps[: STAGES.index(task.stage)]
                ):
                    raise Conflict("Task dependencies are incomplete")
                task.status = "running"
                task.attempt += 1
                task.error = ""
                task.run_token = uuid.uuid4().hex
                task.lease_until = self.clock() + self.lease_seconds
                step = task.steps[STAGES.index(task.stage)]
                step.status = "running"
                step.provider = self.routes[task.stage]
                self._record(
                    db,
                    task,
                    "stage_started",
                    {
                        "stage": task.stage,
                        "attempt": task.attempt,
                        "session_id": self._session(task),
                        "revision": task.revision,
                    },
                )
                return task
        return None

    @staticmethod
    def _session(task: Task) -> str:
        return f"mock-review-{task.id}" if task.stage == "review" else task.session_id

    def tick(self) -> bool:
        task = self.claim()
        if task is None:
            return False
        revision = task.revision
        artifacts: list[Artifact] = []
        try:
            bundled = any(item.name == "bundle.json" for item in task.artifacts)
            if task.stage in {"review", "test", "summarize"}:
                source = (
                    self.workspace.bundle_at(task.id, revision)
                    if bundled
                    else self.workspace.review_source(task.id, revision)
                )
            else:
                source = ""
            context = StepContext(
                task_id=task.id,
                prompt=task.prompt,
                stage=task.stage,
                scenario=task.scenario,
                attempt=task.attempt,
                session_id=self._session(task),
                revision=revision,
                artifact=source,
                approved=task.approved,
                schema=STAGE_SCHEMAS[task.stage],
            )
            provider = task.steps[STAGES.index(task.stage)].provider
            # The route is authoritative: the controller knows which adapter it called,
            # so an adapter cannot relabel itself in the audit trail.
            result = replace(self.registry.run(provider, context), provider=provider)
            # A stale or cancelled call cannot begin additional filesystem work.
            current = self.store.read(task.id)
            if current.run_token != task.run_token or current.status != "running":
                return True
            if self.clock() >= task.lease_until:
                self.expire_leases()
                return True
            if result.outcome == "ok" and task.stage == "implement":
                files = result.data.get("files")
                if isinstance(files, list) and files:
                    accepted = self.workspace.validate_files(files)
                    revision, diff = self.workspace.implement_bundle(task.id, accepted)
                    artifacts = [
                        *(Artifact(item["path"], item["content"]) for item in accepted),
                        Artifact("implementation.diff", diff),
                        Artifact("bundle.json", dumps(accepted)),
                    ]
                else:
                    revision, diff = self.workspace.implement(task.id, result.artifact)
                    artifacts = [
                        Artifact("calculator.py (Mock)", result.artifact),
                        Artifact("implementation.diff", diff),
                    ]
            elif result.outcome == "ok" and task.stage == "test":
                if bundled:
                    if self.sandbox.available():
                        passed, report = self.workspace.integrate_bundle_and_test(
                            task.id, revision, self.sandbox
                        )
                        result = replace(
                            result, outcome="ok" if passed else "failed", summary=report
                        )
                        artifacts = [Artifact("integration-tests.txt", report)]
                    elif self.allow_bundled_tests:
                        passed, report = self.workspace.integrate_bundle_and_test(
                            task.id, revision
                        )
                        result = replace(
                            result, outcome="ok" if passed else "failed", summary=report
                        )
                        artifacts = [Artifact("integration-tests.txt", report)]
                    else:
                        # Running model-authored tests unconfined is not a default we accept.
                        result = replace(
                            result,
                            outcome="policy_blocked",
                            summary="No sandbox backend: refusing to execute model-authored tests",
                        )
                else:
                    passed, report = self.workspace.integrate_and_test(task.id, revision)
                    result = replace(result, outcome="ok" if passed else "failed", summary=report)
                    artifacts = [Artifact("integration-tests.txt", report)]
        except Exception:
            # Never persist arbitrary exception messages: they may carry secrets.
            result = StepResult(outcome="failed", summary="Step failed; no provider was invoked")
        self.finish(task, result, revision, artifacts)
        return True

    def finish(
        self, claimed: Task, result: StepResult, revision: str, artifacts: list[Artifact]
    ) -> None:
        with self.store.transaction() as db:
            task = self.store.get(db, claimed.id)
            if task.status != "running" or task.run_token != claimed.run_token:
                return
            if self.clock() >= task.lease_until:
                result = replace(result, outcome="timed_out", summary="Step deadline exceeded")
            task.run_token = ""
            task.lease_until = 0
            step = task.steps[STAGES.index(task.stage)]
            task.revision = revision
            task.artifacts.extend(artifacts)
            # Only the deterministic Mock adapter may claim zero cost; a real endpoint
            # reporting a number it cannot substantiate fails closed.
            if result.provider == "mock" and result.cost_usd != 0:
                result = StepResult(outcome="failed", summary="Unexpected cost; execution blocked")
            step.provider = result.provider
            step.model = result.model
            step.session_id = result.session_id
            step.summary = result.summary
            step.revision = revision
            if result.outcome == "ok":
                step.status = "completed"
                self._record(
                    db,
                    task,
                    "stage_completed",
                    {
                        "stage": task.stage,
                        "summary": step.summary,
                        "revision": revision,
                        "session_id": self._session(task),
                        "cost_usd": task.cost_usd,
                        "provider": result.provider,
                        "model": result.model,
                        "usage": result.usage,
                    },
                )
                index = STAGES.index(task.stage)
                if index == len(STAGES) - 1:
                    task.status = "completed"
                    task.result = "Mock 示例已完成，审查和测试通过。"
                else:
                    task.stage = STAGES[index + 1]
                    task.status = "queued"
                    task.attempt = 0
                    task.next_run_at = 0
                self._record(db, task, "state_changed")
                return
            allowed = {
                "waiting_approval",
                "policy_blocked",
                "cancelled",
                "rate_limited",
                "quota_exhausted",
                "auth_required",
                "timed_out",
                "failed",
            }
            task.status = result.outcome if result.outcome in allowed else "failed"
            task.error = result.summary
            step.status = task.status
            if task.status == "rate_limited":
                task.next_run_at = self.clock() + max(1, min(result.retry_after, 300))
            if task.status == "waiting_approval":
                operation = (
                    "Mock：在此任务独立 worktree 写入固定 calculator.py 示例并创建本地提交；"
                    "不启动任何厂商、不推送，费用 USD 0。"
                )
                digest = hashlib.sha256(
                    dumps(
                        {
                            "task_id": task.id,
                            "stage": task.stage,
                            "operation": operation,
                            "revision": task.revision,
                            "scenario": task.scenario,
                        }
                    ).encode()
                ).hexdigest()
                task.approval = Approval(uuid.uuid4().hex, operation, digest, task.revision)
            self._record(db, task, "state_changed")

    def action(self, task_id: str, action: str) -> Task:
        with self.store.transaction() as db:
            task = self.store.get(db, task_id)
            if action == "cancel":
                if task.status == "cancelled":
                    return task
                if task.status in TERMINAL:
                    raise Conflict("Terminal tasks cannot be cancelled")
                task.status = "cancelled"
                if task.approval and task.approval.status == "pending":
                    task.approval.status = "cancelled"
            elif action == "pause":
                if task.status == "paused":
                    return task
                if task.status not in {"running", "queued", "rate_limited"}:
                    raise Conflict("Only runnable tasks can be paused")
                task.status = "paused"
            elif action == "resume":
                if task.status not in RESUMABLE:
                    raise Conflict("This state cannot be resumed")
                # A rate limit must respect the vendor's retry window, even after user resume.
                task.status = "queued"
                task.error = ""
            else:
                raise ValueError("Unknown action")
            task.run_token = ""
            task.lease_until = 0
            task.steps[STAGES.index(task.stage)].status = task.status
            self._record(db, task, "user_action", {"action": action, "status": task.status})
            return task

    def approve(self, task_id: str, approval_id: str, decision: str, digest: str) -> Task:
        if decision not in {"approve", "deny"}:
            raise ValueError("Unknown approval decision")
        with self.store.transaction() as db:
            task = self.store.get(db, task_id)
            approval = task.approval
            if (
                approval is None
                or approval.id != approval_id
                or approval.operation_digest != digest
            ):
                raise Conflict("Approval does not match this exact operation")
            desired = "approved" if decision == "approve" else "denied"
            if approval.status == desired:
                return task
            if approval.status != "pending" or task.status != "waiting_approval":
                raise Conflict("Approval is no longer pending")
            if approval.revision != task.revision:
                raise Conflict("Revision changed; approval is invalid")
            approval.status = desired
            task.approved = decision == "approve"
            task.status = "queued" if task.approved else "cancelled"
            task.error = "" if task.approved else "User denied this operation"
            task.steps[STAGES.index(task.stage)].status = task.status
            self._record(
                db,
                task,
                "approval_decided",
                {
                    "approval_id": approval_id,
                    "decision": decision,
                    "operation_digest": digest,
                    "actor": "user",
                },
            )
            return task
