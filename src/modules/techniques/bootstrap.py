"""Wire the same technique service into operation startup and standalone use."""

from modules.techniques.repository import TechniqueRepository
from modules.techniques.research import WebResearch
from modules.techniques.roles import TechniqueRoleRunner
from modules.techniques.service import TechniqueService
from modules.techniques.storage import TechniqueStore


def operation_techniques(memory_client, config, search_tool, *, synchronize=True):
    from modules.techniques.debug import TechniqueExports
    from modules.tools.memory import _operation_output_root, get_application_database_path

    repository = TechniqueRepository(get_application_database_path())
    store = TechniqueStore(memory_client.qdrant, memory_client.embeddings, memory_client.config, repository)
    runner = TechniqueRoleRunner(config.provider, config.model_id)
    service = TechniqueService(store, runner, WebResearch(runner, search_tool))
    service.exports = TechniqueExports(_operation_output_root())
    from urllib.parse import urlsplit

    target = str(config.target)
    hostname = urlsplit(target if "://" in target else "//" + target).hostname or ""
    service.private_values = tuple(value for value in (target, hostname) if len(value) > 3)

    if synchronize:
        from modules.prompts.factory import get_module_loader

        service.synchronize_module(get_module_loader(), config.module)
    return service
