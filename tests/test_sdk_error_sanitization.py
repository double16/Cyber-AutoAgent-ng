from strands.types.exceptions import MaxTokensReachedException

from modules.agents.multi_agent_workflow import TaskPromptBuildError
from modules.utils.sdk_error_sanitization import sanitize_sdk_error

SDK_URL = "https://strandsagents.com/docs/user-guide/concepts/agents/agent-loop/#maxtokensreachedexception"


def test_sanitize_sdk_error_removes_urls_and_preserves_context():
    error = MaxTokensReachedException(f"generation stopped; see {SDK_URL}")

    message = sanitize_sdk_error(error)

    assert SDK_URL not in message
    assert "[sdk-url-omitted]" in message
    assert "generation stopped" in str(error)


def test_sanitize_sdk_error_handles_wrapped_sdk_exception():
    sdk_error = MaxTokensReachedException(f"see {SDK_URL}")
    error = TaskPromptBuildError("prompt builder failed", repairable=True)
    error.__cause__ = sdk_error
    error.args = (f"prompt builder failed: {SDK_URL}",)

    assert sanitize_sdk_error(error) == "prompt builder failed: [sdk-url-omitted]"
    assert SDK_URL not in str(sdk_error)


def test_sanitize_sdk_error_does_not_change_application_exception_urls():
    target_url = "https://target.example.test/api"
    error = ValueError(f"target request failed: {target_url}")

    assert sanitize_sdk_error(error) == str(error)
