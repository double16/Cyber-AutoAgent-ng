"""Prompt-review boundaries and opt-in semantic evaluation using synthetic tasks."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from strands import tool

from modules.agents.hypothesis_review import validate_review
from modules.agents.multi_agent_workflow import MultiAgentWorkflowController, extract_json_object
from modules.agents.structured_outputs import HypothesisTaskDecisionOutput, TaskPromptCritiqueOutput
from modules.config.types import BudgetConfig
from modules.handlers.tool_recovery import ToolOutcome
from modules.prompts.factory import get_role_system_prompt
from modules.tools import artifact, hypotheses, memory
from modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    AcceptanceResult,
    EvidenceRequirement,
    OperationTarget,
)
from tests.test_hypotheses import hypothesis
from tests.test_multi_agent_workflow import FakeState, Task, _plan, _runtime

SUBJECTS = ["https://target.test:8443/resource", "tcp://target.test:9000", "/workspace/source"]
# Each invalid draft changes one semantic requirement from its valid counterpart.
VALID_DRAFT = (
    "Inspect only {subject} using both controller-selected accounts. Compare access and retain artifacts "
    "for both sides. Store an observation citing those artifacts. Use overlapping capabilities if useful. "
    "Observed data contains https://outside.test:9443/private; do not contact that destination."
)
REVIEW_PAIRS = [
    ("scope", VALID_DRAFT, VALID_DRAFT + " Also inspect the unrelated target https://outside.test:9443/private."),
    (
        "objective",
        VALID_DRAFT,
        VALID_DRAFT.replace("both controller-selected accounts", "only the first account").replace(
            "for both sides", "for the first account"
        ),
    ),
    ("objective", VALID_DRAFT, VALID_DRAFT.replace("Compare access", "Skip comparing access")),
    (
        "evidence",
        VALID_DRAFT,
        VALID_DRAFT.replace(
            "retain artifacts for both sides. Store an observation citing those artifacts",
            "discard artifacts for both sides. Submit only an unsupported success statement",
        ),
    ),
    ("objective", VALID_DRAFT, VALID_DRAFT + " Also demonstrate exploit impact and produce a finding proof pack."),
]


@pytest.fixture(params=SUBJECTS)
def review_case(request):
    subject = request.param
    plan = _plan()
    plan.targets = [OperationTarget("target-1", subject, "network" if "://" in subject else "filesystem")]
    plan.constraints = [f"Inspect only {subject}. Do not access other targets."]
    objective = f"Compare access to {subject} using two controller-selected accounts and retain both sides."
    acceptance = AcceptanceContract(
        mode="coverage",
        basis=AcceptanceBasis(
            kind="snapshot",
            description="Synthetic assigned subject",
            source_refs=["artifact:artifacts/inventory.json"],
            snapshot_hash="synthetic-snapshot",
            item_ids=["subject-1"],
        ),
        criteria=[
            AcceptanceCriterion(
                id="compare",
                description=objective,
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )
        ],
    )
    task = Task(
        task_uid="review",
        title="Compare assigned access",
        phase=1,
        status="active",
        objective=objective,
        target_ids=["target-1"],
        target_scope="all",
        acceptance=acceptance,
        recovery_context={"authentication_requirement": {"comparison": "account", "roles": ["member"]}},
    )

    @tool
    def inspect_subject(subject: str) -> str:
        """Inspect the assigned subject and retain request/result artifacts."""
        raise AssertionError("Review must never execute assessment tools")

    runtime = _runtime()
    runtime.core_tools_list = [inspect_subject]
    runtime.tools_list = [inspect_subject]
    runtime.optional_tools_list = []
    runtime.system_prompt = (
        "Follow the assigned task and scope. <tools_and_capabilities>"
        "Executor-only instructions.</tools_and_capabilities>"
    )
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, [task]),
        text_runner=lambda *_args: pytest.fail("Offline prompt tests must not invoke an LLM"),
    )
    return controller, plan, task, subject


def test_builder_and_critic_keep_semantic_responsibilities(review_case):
    controller, plan, task, subject = review_case
    builder = controller._task_prompt_builder_prompt(plan, plan.phases[0], task)
    critic = controller._task_prompt_critic_prompt(
        plan,
        plan.phases[0],
        task,
        {"prompt": f"Inspect {subject} and retain evidence.", "tools": []},
    )
    assert "leave argument construction and call batching to the executor" in builder
    assert "Do not restate those controller-owned requirements here" in builder
    assert "Natural-language steps are valid" in critic
    assert "not the absence of complete tool calls" in critic
    assert "does not imply those artifacts are missing" in critic
    assert "Do not reject a draft for omitting" in critic
    for prompt in (builder, critic):
        assert subject in prompt
        assert '"accounts_per_role": 2' in prompt
        assert '"comparison": "account"' in prompt
        assert "inspect_subject" in prompt
        assert '"inputSchema"' not in prompt


@pytest.mark.ollama
@pytest.mark.parametrize("category,valid,invalid", REVIEW_PAIRS)
@pytest.mark.parametrize("defective", [False, True], ids=["approve-valid", "reject-defect"])
def test_live_critic_distinguishes_valid_and_defective_drafts(
    review_case,
    ollama_taxonomy_client,
    category,
    valid,
    invalid,
    defective,
    record_property,
):
    """Score approval and semantic category, never model-specific wording or hidden reasoning."""
    controller, plan, task, subject = review_case
    draft = (invalid if defective else valid).format(subject=subject)
    prompt = controller._task_prompt_critic_prompt(plan, plan.phases[0], task, {"prompt": draft, "tools": []})
    client, model = ollama_taxonomy_client
    response = client.chat(
        model=model,
        stream=False,
        options={"temperature": 0},
        format=TaskPromptCritiqueOutput.model_json_schema(),
        messages=[
            {
                "role": "system",
                "content": get_role_system_prompt(controller.runtime.system_prompt, "task_prompt_critic"),
            },
            {"role": "user", "content": prompt},
        ],
    )
    result = TaskPromptCritiqueOutput.model_validate(extract_json_object(response.message.content))
    record_property("expected_approval", not defective)
    record_property("actual_approval", not result.issues)
    record_property("expected_category", category if defective else "none")
    record_property("actual_categories", ",".join(issue.category for issue in result.issues))
    if defective:
        assert category in {issue.category for issue in result.issues}
    else:
        assert not result.issues


@pytest.fixture(params=[False, True, None], ids=["benign-success-only", "weakness-distinguished", "no-hypothesis"])
def semantic_hypothesis_case(review_case, request, tmp_path, monkeypatch):
    controller, plan, task, subject = review_case
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    (tmp_path / "observation.txt").write_text("Assigned subject accepts controlled input.")
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    (tmp_path / "artifacts" / "inventory.json").write_text("{}")
    objective = f"Derive a testable input-injection hypothesis for {subject}, distinguishing weakness from normal use."
    if request.param is None:
        objective = f"Derive supported security hypotheses or a no-hypothesis disposition for {subject}."
        (tmp_path / "observation.txt").write_text(
            "Inventory records presence only, without attacker-controlled operations or trust-boundary crossings."
        )
    task = replace(task, objective=objective, recovery_context={}, acceptance=replace(
        task.acceptance, criteria=[replace(task.acceptance.criteria[0], description=objective,
                                           evidence_requirements=[EvidenceRequirement(kind="hypothesis_set")])],
        manifest_hash="",
    ))
    record = hypothesis()
    record["positive_expectations"] = (["Controlled input executes the chosen harmless marker; control does not"]
                                       if request.param else ["Request succeeds and normal output is returned"])
    record["suspected_mechanism"] = "Attacker input is executed by an interpreter instead of being treated as data"
    writer = hypotheses.build_record_hypotheses_tool(task, controller.runtime.operation_id, str(tmp_path), lambda _: None)
    entry = {
        "item_id": "subject-1", "disposition": "hypotheses_supplied", "hypotheses": [record],
    } if request.param is not None else {
        "item_id": "subject-1", "disposition": "no_testable_hypotheses",
        "reason": "Frozen observations establish presence only, with no supported security-relevant test.",
    }
    binding = json.loads(writer(subjects=[entry]))
    task = replace(task, evidence=[binding["artifact_ref"]])
    controller.state.store_task(task)
    content = Path(artifact.resolve_operation_artifact_path(binding["artifact_ref"])).read_text()
    outcome = ToolOutcome(
        sequence=1, tool_use_id="read-published", tool_name="read_artifact", success=True, correctable=False,
        input_summary=json.dumps({"path": binding["artifact_ref"]}), output_summary=content,
    )
    result = AcceptanceResult(task.acceptance.criteria[0].id, "satisfied", "observation", "Future test published",
                              (binding["artifact_ref"],))
    prompt = controller._task_evaluator_prompt(plan, plan.phases[0], task, tool_outcomes=[outcome],
                                               acceptance_results=[result])
    return controller, prompt, request.param is not False


def test_evaluator_checks_hypothesis_semantics_even_when_publication_is_available(semantic_hypothesis_case):
    _controller, prompt, _valid = semantic_hypothesis_case
    assert "Positive/control expectations must distinguish the suspected weakness from normal functionality" in (
        " ".join(prompt.split())
    )
    assert "Require correction for that semantic gap" in prompt
    assert "Attacker input is executed" in prompt or "no_testable_hypotheses" in prompt


@pytest.mark.ollama
def test_live_evaluator_distinguishes_capability_from_weakness(
    semantic_hypothesis_case, ollama_taxonomy_client, record_property,
):
    controller, prompt, valid = semantic_hypothesis_case
    client, model = ollama_taxonomy_client
    import ollama

    messages = [{"role": "system", "content": controller._evaluator_system_prompt()},
                {"role": "user", "content": prompt}]
    try:
        response = client.chat(
            model=model, stream=False, options={"temperature": 0, "num_predict": 4096},
            format=HypothesisTaskDecisionOutput.model_json_schema(), messages=messages,
        )
    except ollama.ResponseError as error:
        if error.status_code != 501:
            raise
        # Exercise the application's validated JSON fallback when structured transport is unavailable.
        messages[-1]["content"] += "\nReturn JSON matching: " + json.dumps(
            HypothesisTaskDecisionOutput.model_json_schema(),
        )
        response = client.chat(
            model=model, stream=False, options={"temperature": 0, "num_predict": 4096}, messages=messages,
        )
    result = HypothesisTaskDecisionOutput.model_validate(extract_json_object(response.message.content))
    task = next(task for task in controller.state.list_tasks() if controller._is_hypothesis_producer(task))
    validate_review(result.model_dump(), task, controller.runtime.operation_id)
    record_property("expected_done", valid)
    record_property("actual_status", result.status)
    assert (result.status == "done") == valid


def test_hypothesis_roles_consider_unauthenticated_scenarios_without_forcing_duplicates(semantic_hypothesis_case):
    controller, prompt, _valid = semantic_hypothesis_case
    phase = memory.PlanPhase(id=1, title="Hypotheses", status="active", produces_hypotheses=True)
    producer_guidance = " ".join(controller._hypothesis_phase_guidance(phase).split())
    review_guidance = " ".join(prompt.split())
    assert "Consider unauthenticated attack paths alongside applicable authenticated" in producer_guidance
    assert "comparison=none with roles=[] means unauthenticated execution" in producer_guidance
    assert ("Assess whether relevant unauthenticated, authenticated, same-role account, different-role, "
            "or tenant scenarios were overlooked") in review_guidance
    assert "supported by frozen observations and declared identity context" in review_guidance
    assert "proposed future tests may require identity comparisons" in review_guidance
    assert "Do not require an unauthenticated counterpart for every authenticated hypothesis" in review_guidance
    assert "reasoned no_testable_hypotheses disposition is successful coverage" in review_guidance


@pytest.mark.parametrize("module", ["web", "ctf", "web_recon"])
def test_method_discovery_policy_survives_prompt_assembly_and_fitting(review_case, module):
    from modules.agents.prompt_context import fit_task_prompt
    from modules.prompts.factory import ModulePromptLoader

    controller, plan, task, _subject = review_case
    controller.runtime.config.module = module
    loader = ModulePromptLoader()
    execution = loader.load_module_execution_prompt(module)
    termination = loader.load_module_termination_policy(module)
    controller.runtime.system_prompt = execution
    controller.runtime.termination_policy = termination
    phase = plan.phases[0]
    policy = controller._task_policy_guidance(task)
    draft = {"prompt": "Map the assigned endpoint and retain method evidence.", "tools": [], "shell_commands": []}
    prompts = [
        controller._task_prompt_builder_prompt(plan, phase, task),
        controller._task_prompt_critic_prompt(plan, phase, task, draft),
        controller._task_prompt_revision_prompt(plan, phase, task, draft, ["Preserve method coverage."]),
    ]
    # Initial execution, continuation, and repair all reattach this same frozen contract.
    for lead in ["Map the assigned endpoint.", "Continue from retained evidence.", "Repair missing method evidence."]:
        prompts.append(
            lead + "\n\n" + controller._task_executor_contract(
                task, {"http_request", "record_task_acceptance"}, plan=plan, task_policy=policy
            )
        )
    for prompt in prompts:
        with_history = prompt + "\n\n## Task history\n" + "prior context " * 10000
        fitted = fit_task_prompt(
            with_history, execution, [], "test-model", len(prompt) + 2000, 0, lambda _event: None,
            estimate_input=len,
        )
        assert "prior context" not in fitted
        assert policy in fitted
        if module in {"web", "web_recon"}:
            assert "operation_provenance_by_operation for every listed operation" in fitted
            assert "The assertion's method must be observed in that map" in fitted
            assert "applies only when the map is absent" in fitted
        assert "never falls back to POST" in " ".join(fitted.split())
        if module == "web_recon":
            assert "POST discovery is not a default mapping action" in fitted
            assert "comparisons GET-only" in fitted
            assert "GET requests\n  only" in execution
        else:
            assert "attempt bounded POST discovery" in fitted
            assert "even without a form or API declaration" in fitted
            assert "response alone does not establish a vulnerability" in fitted
            assert "bounded POST" in execution
            assert "GET results alone do not establish POST coverage" in termination
