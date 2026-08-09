export type ToolDiscoveryEvent = {
  type?: string;
  message?: unknown;
  tool_name?: unknown;
  description?: unknown;
  tool_count?: unknown;
};

const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";

export const formatToolDiscoveryEvent = (event: ToolDiscoveryEvent | null | undefined): string | null => {
  switch (event?.type) {
    case "tool_discovery_start":
      return `🔎 ${text(event.message) || "Loading cybersecurity assessment tools"}`;
    case "tool_available": {
      const toolName = text(event.tool_name) || "unnamed tool";
      const description = text(event.description);
      return description ? `  🔧 ${toolName} (${description})` : `  🔧 ${toolName}`;
    }
    case "tool_unavailable": {
      const toolName = text(event.tool_name) || "unnamed tool";
      const description = text(event.description);
      return `  ⛔ ${toolName}${description ? ` (${description})` : ""} - unavailable`;
    }
    case "environment_ready": {
      const toolCount = Number(event.tool_count);
      const fallback = Number.isFinite(toolCount)
        ? `Environment ready - ${toolCount} cybersecurity tools loaded`
        : "Environment ready";
      return `🟢 ${text(event.message) || fallback}`;
    }
    default:
      return null;
  }
};
