# Documentation

This directory contains comprehensive technical documentation for Cyber-AutoAgent. Each document covers specific aspects of the system architecture, operation, and deployment.

## Documentation Structure

### Core Architecture

**[architecture.md](architecture.md)**
- Python-owned multi-agent workflow architecture
- Strands framework integration
- Role-agent lifecycle and restricted tool selection
- Metacognitive reasoning patterns
- Memory integration

### System Components

**[memory.md](memory.md)**
- Memory system architecture
- Backend configurations (FAISS, OpenSearch, Mem0 Platform)
- Evidence categorization and storage
- Reflection and planning systems
- Query optimization

**[prompt_management.md](prompt_management.md)**
- Module-based prompt system
- Prompt loading and composition
- Tool discovery mechanisms
- Report generation integration

**[task_tracking.md](task_tracking.md)**
- Tasks are captured from new evidence
- Python owns phase/task activation and completion
- Role agents execute short, defined objectives
- Completion is evaluator-driven with status + reason
- Context pruning preserves the active task and the evidence needed to continue
- Pending work can persist across runs to enable long-lived coverage goals
- Prompt adaptation happens inside the Python-owned role-agent workflow

### Interface and User Experience

**[terminal-frontend.md](terminal-frontend.md)**
- React-based terminal interface
- Event-driven architecture
- Service layer implementation
- State management patterns

**[user-instructions.md](user-instructions.md)**
- Command line operation
- Module selection
- Provider configuration
- Output management
- MCP configuration
- Troubleshooting

### Operations

**[observability-evaluation.md](observability-evaluation.md)**
- Langfuse tracing integration
- Ragas evaluation metrics
- Performance monitoring
- Automated scoring

**[deployment.md](deployment.md)**
- Docker deployment
- Production configuration
- Security considerations
- Troubleshooting guides

## Quick Navigation

| Documentation Need            | Recommended Document                                       |
|-------------------------------|------------------------------------------------------------|
| Understanding agent design    | [architecture.md](architecture.md)                         |
| Running assessments           | [user-instructions.md](user-instructions.md)               |
| Configuring memory            | [memory.md](memory.md)                                     |
| Creating custom modules       | [operation_plugins.md](operation_plugins.md)                |
| Monitoring operations         | [observability-evaluation.md](observability-evaluation.md) |
| Production deployment         | [deployment.md](deployment.md)                             |
| Understanding UI architecture | [terminal-frontend.md](terminal-frontend.md)               |

## Getting Started

1. **New Users**: Start with [user-instructions.md](user-instructions.md) for operational guidance
2. **Developers**: Review [architecture.md](architecture.md) for system design
3. **Operations**: Consult [deployment.md](deployment.md) for production setup
4. **Module Developers**: See [operation_plugins.md](operation_plugins.md) for custom modules and custom tools

## Document Conventions

**Code Examples**: Examples are limited to supported commands, configuration, and module custom-tool implementations
**Diagrams**: Mermaid diagrams illustrate architecture and flow patterns
**Configuration**: Examples use supported configuration fields and realistic placeholder values
**Cross-References**: Links connect related concepts across documents

## Additional Resources

**Main Project README**: [../README.md](../README.md) - Project overview and quick start
**Source Code**: [../src/](../src/) - Implementation details
**Operation Modules**: [../src/modules/operation_plugins/](../src/modules/operation_plugins/) - Available modules

## Contributing to Documentation

When updating documentation:
- Maintain professional technical tone
- Verify all code examples
- Update cross-references
- Include diagrams where helpful
- Document actual implementation, not planned features
- Follow existing style and structure

## Documentation Status

This documentation reflects the current implementation state. Features marked as "future" or "planned" are explicitly noted. All examples and configurations have been verified against the codebase.
