# Operation Modules

Operation modules extend Cyber-AutoAgent with domain-specific prompts, optional custom tools, and report guidance.
They are loaded by `ModulePromptLoader` in `src/modules/prompts/factory.py`.

## Module structure

```text
<module>/
├── module.yaml
├── execution_prompt.md
├── termination_policy.md
├── report_prompt.md
├── report_agent_executive_system_prompt.md
├── report_agent_finding_system_prompt.md
├── report_agent_observation_system_prompt.md
├── report_agent_appendix_system_prompt.md
└── tools/
    ├── __init__.py
    └── custom_tool.py
```

`module.yaml` is required. Prompt files and `tools/` are optional when inherited from another module.

### Manifest

Use the fields supported by the bundled manifests:

```yaml
name: custom_module
version: 1.0.0
description: Specialized assessment for a custom domain
license: Apache-2.0
cognitive_level: 3
extend:
  - web
capabilities:
  - Domain-specific assessment
supported_targets:
  - web-application
tools:
  - custom_scanner
configuration:
  approach: Evidence-driven assessment
```

`tools` is an allowlist and is not inherited. If it is omitted, the module uses the default tool selection. The
`extend` list is transitive and is checked in order.

### Prompt files

- `execution_prompt.md` defines domain methodology, access rules, evidence requirements, and prohibited actions.
- `termination_policy.md` defines evidence-backed completion outcomes.
- `report_prompt.md` provides domain-specific report guidance.
- Report-agent prompt files provide section-specific guidance for executive, finding, observation, and appendix output.

Python owns phase, task, evaluation, and operation transitions. Module prompts guide role agents but do not grant them
control over those transitions. Finding validation remains an independent workflow task; custom tools should produce
durable evidence for that task rather than deciding that a finding is verified.

## Discovery and inheritance

The loader searches these roots in order:

1. `CYBER_PLUGIN_PATH`, using `:` as the path separator.
2. `~/.cyber-autoagent/modules/`.
3. Bundled `src/modules/operation_plugins/`.

The Docker Compose file mounts external plugins and home plugins into the first two categories. Discovery recursively
searches each root for a directory containing `module.yaml` or `module.yml`.

For inherited content, a child prompt overrides the first matching parent prompt. Tool directories are inherited, with
the child or first parent occurrence winning for duplicate tool names. Inheritance cycles are rejected.

## Custom tools

Custom tools are Python files under `tools/` with Strands-decorated functions. Keep the implementation focused on a
single capability, validate inputs, handle operational errors, and return structured evidence where appropriate.

```python
from strands import tool


@tool
def custom_scanner(target: str, depth: int = 3) -> str:
    """Run a domain-specific scan against an authorized target."""
    if not target.strip():
        raise ValueError("target is required")
    return f"Scan completed for {target} at depth {depth}"
```

### Creating a sub-agent from a custom tool

The agent factory is attached to registered custom tools that need to create a focused sub-agent. Use it when the
tool needs a purpose-built model, prompt, or tool set for a bounded specialist action:

```python
import json
from typing import Any, Dict

from strands import tool


@tool
def custom_tool() -> Dict[str, Any]:
    agent_factory = getattr(custom_tool, "agent_factory", None)
    assert agent_factory is not None

    agent = agent_factory(
        name="custom-specialist",
        agent_type="custom_tool",
        model_spec={"model_settings": {"model_id": "purpose-built-model", "params": {"temperature": 0.8}}},
        # Remaining arguments are passed to the strands.Agent constructor.
        system_prompt="Perform the bounded specialist task and return JSON evidence.",
        tools=[],
    )
    result = agent("Complete the specialist task.")
    return json.loads(str(result))
```

The agent factory discovers module tool files, imports decorated functions, and applies the manifest allowlist. Module
documentation should describe the tool's purpose and inputs; users do not need to invoke the internal discovery
mechanism directly.

## Bundled modules

| Module | Domain | Custom tools |
|---|---|---:|
| `web` | Web application and network security | 4 |
| `web_recon` | Web reconnaissance, extending `web` | 0 |
| `ctf` | CTF challenge solving, extending `web` | 1 |
| `threat_emulation` | Threat emulation and ATT&CK-oriented workflows | 0 |
| `context_navigator` | Post-access environment discovery | 0 |
| `code_security` | Static code security analysis | 0 |

The repository does not encode a production/experimental status field in module manifests. Treat module maturity as a
project-level decision rather than a loader-enforced property.

## Implementation reference

- Module loading: `src/modules/prompts/factory.py` (`ModulePromptLoader`)
- Agent integration: `src/modules/agents/cyber_autoagent.py` (`create_agent`)
- Report generation: `src/modules/handlers/report_generator.py`
- Bundled modules: `src/modules/operation_plugins/`
