from modules.tools.memory import TaskProposal, TaskProposalLimits

def test_task_proposal_limits_relaxation():
    # description should be allowed in limits
    limits = TaskProposalLimits(max_requests=50, description="Safety limit")
    assert limits.max_requests == 50
    assert limits.description == "Safety limit"

def test_task_proposal_normalization_limits_list():
    # limits as [] should be converted to {}
    data = {
        "title": "Test Task",
        "objective": "Test Objective",
        "methods": ["test"],
        "limits": [],
        "criteria": [{"description": "test criterion"}]
    }
    proposal = TaskProposal(**data)
    assert isinstance(proposal.limits, TaskProposalLimits)
    assert proposal.limits.max_requests == 50  # Default value

def test_task_proposal_normalization_criteria_strings():
    # criteria as ["string"] should be converted to [{"description": "string"}]
    data = {
        "title": "Test Task",
        "objective": "Test Objective",
        "methods": ["test"],
        "criteria": ["test criterion string"]
    }
    proposal = TaskProposal(**data)
    assert len(proposal.criteria) == 1
    assert proposal.criteria[0].description == "test criterion string"

def test_task_proposal_normalization_extra_fields():
    # 'name' and 'work_type' should be removed before validation
    data = {
        "title": "Test Task",
        "objective": "Test Objective",
        "name": "Old Name",
        "work_type": "some_type",
        "methods": ["test"],
        "criteria": [{"description": "test criterion"}]
    }
    proposal = TaskProposal(**data)
    # If we reached here without ValidationError, it works.
    assert proposal.title == "Test Task"


def test_task_proposal_normalizes_name_as_title_alias():
    proposal = TaskProposal(
        name="Legacy task title",
        objective="Test Objective",
        methods=["test"],
        criteria=[{"description": "test criterion"}],
    )

    assert proposal.title == "Legacy task title"

def test_task_proposal_description_alias():
    # 'description' should be moved to 'objective'
    data = {
        "title": "Test Task",
        "description": "Test Description as Objective",
        "methods": ["test"],
        "criteria": [{"description": "test criterion"}]
    }
    proposal = TaskProposal(**data)
    assert proposal.objective == "Test Description as Objective"

def test_task_proposal_limits_description_field():
    # 'description' inside limits should be allowed
    data = {
        "title": "Test Task",
        "objective": "Test Objective",
        "methods": ["test"],
        "limits": {"max_requests": 10, "description": "some limit"},
        "criteria": [{"description": "test criterion"}]
    }
    proposal = TaskProposal(**data)
    assert proposal.limits.max_requests == 10
    assert proposal.limits.description == "some limit"
