# Cyber-AutoAgent Prompts Module

## Overview

The `prompts` module is the central nervous system for all language-based instructions given to the Cyber-AutoAgent. It is responsible for dynamically constructing and serving every prompt, from the agent's core persona and system instructions to the detailed, context-aware prompts required for specialized operational modules and final report generation.

This module is designed for maximum modularity and extensibility, allowing developers to easily create, modify, and plug in new capabilities without altering the core agent logic.

---

## File Structure

```
src/modules/prompts/
├── templates/
│   ├── system_prompt.md
│   ├── tools_guide.md
│   ├── report_agent_executive_system_prompt.md
│   ├── report_agent_finding_system_prompt.md
│   ├── report_agent_observation_system_prompt.md
│   └── report_agent_appendix_system_prompt.md
├── __init__.py
├── factory.py
└── README.md
```

---

## Core Components

### `factory.py`
This is the heart of the module. It contains the primary logic for prompt construction and the loading of external modules.

- **`get_system_prompt(...)`**: Assembles the main system prompt for the agent by combining the base persona, workflow, tool guides, and any module-specific execution guidance.
- **`get_report_generation_prompt(...)`**: Constructs the detailed prompt used by a specialized AI agent to write the final security report.
- **`ModulePromptLoader` (Class)**: The engine for our plugin architecture. It discovers modules, resolves prompt inheritance, and discovers allowlisted custom tools.

### `templates/` Directory
This directory stores the Markdown and text templates that form the building blocks of all prompts. Externalizing these templates allows for easy modification of the agent's behavior and report structure without touching Python code.

- **`system_prompt.md`**: Defines the agent's core persona, high-level objectives, and rules of engagement.
- **`tools_guide.md`**: Provides the agent with a general manual on how to use its built-in tools and capabilities effectively.
- **`report_agent_executive_system_prompt.md`**: Guidance for the executive report section.
- **`report_agent_finding_system_prompt.md`**: Guidance for finding synthesis.
- **`report_agent_observation_system_prompt.md`**: Guidance for observation synthesis.
- **`report_agent_appendix_system_prompt.md`**: Guidance for report appendix content.

---

## Plugin Loading Workflow (Operation Modules)

The agent's capabilities are extended through **Operation Modules**, which are self-contained plugins. The `ModulePromptLoader` in `factory.py` manages them as follows:

1.  **Discovery**: The loader scans paths in `CYBER_PLUGIN_PATH`, `~/.cyber-autoagent/modules/`, and the bundled `src/modules/operation_plugins/` directory. Discovery is recursive and requires a `module.yaml` or `module.yml` manifest.
2.  **Resolution**: The loader reads the manifest, follows its transitive `extend` list, and resolves local prompt files with child-first precedence.
3.  **Loading**: When a module is selected for an operation, the loader reads its files:
    - **`module.yaml`**: Contains metadata like the module's `name` and `description`.
    - **`execution_prompt.md`**: Provides specific instructions, rules, and context for the agent. This content is injected directly into the main system prompt, guiding the agent's behavior for the specific task.
    - **`termination_policy.md`**: Provides specific instructions and rules for determining when the operation is complete.
    - **`report_prompt.md`**: Supplies module-specific guidance to report generation.
    - **`/tools` sub-directory**: Python files are discovered as custom tools. The manifest `tools` key controls the allowlist and is not inherited.

This architecture allows the agent to dynamically adapt its core instructions and toolset based on the specific operation it is tasked with.
