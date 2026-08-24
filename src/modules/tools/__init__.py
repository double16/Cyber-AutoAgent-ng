"""Tools module for Cyber-AutoAgent."""

from modules.tools.artifact import create_artifact_reader
from modules.tools.browser import (
    browser_evaluate_js,
    browser_get_cookies,
    browser_get_page_html,
    browser_goto_url,
    browser_observe_page,
    browser_perform_action,
    browser_set_headers,
    initialize_browser,
)
from modules.tools.channels import (
    channel_close,
    channel_close_all,
    channel_create_forward,
    channel_create_reverse,
    channel_poll,
    channel_send,
    channel_status,
)
from modules.tools.mcp import (
    discover_mcp_tools,
)
from modules.tools.memory import (
    QdrantMemoryClient,
    create_tasks,
    get_memory_client,
    initialize_memory_system,
    memory_list,
    memory_retrieve,
    record_finding_validation,
    record_objective_validation,
    store_finding,
    store_knowledge,
    store_objective_candidate,
    store_observation,
)
from modules.tools.oast import (
    close_oast_providers,
    oast_clear_http_responses,
    oast_endpoints,
    oast_health,
    oast_poll,
    oast_register_http_response,
)
from modules.tools.recon_inventory_manifest import recon_output_to_inventory_manifest

__all__ = [
    "QdrantMemoryClient",
    "browser_evaluate_js",
    "browser_get_cookies",
    "browser_get_page_html",
    "browser_goto_url",
    "browser_observe_page",
    "browser_perform_action",
    "browser_set_headers",
    "channel_close",
    "channel_close_all",
    "channel_create_forward",
    "channel_create_reverse",
    "channel_poll",
    "channel_send",
    "channel_status",
    "close_oast_providers",
    "create_artifact_reader",
    "create_tasks",
    "discover_mcp_tools",
    "get_memory_client",
    "initialize_browser",
    "initialize_memory_system",
    "memory_list",
    "memory_retrieve",
    "oast_clear_http_responses",
    "oast_endpoints",
    "oast_health",
    "oast_poll",
    "oast_register_http_response",
    "recon_output_to_inventory_manifest",
    "record_finding_validation",
    "record_objective_validation",
    "store_finding",
    "store_knowledge",
    "store_objective_candidate",
    "store_observation",
]
