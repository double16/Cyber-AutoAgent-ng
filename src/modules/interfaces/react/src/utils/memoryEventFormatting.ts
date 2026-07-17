import {formatToolInput} from './toolFormatters.js';

const TYPED_MEMORY_TOOLS = new Set([
  'store_observation',
  'store_knowledge',
  'store_finding',
  'record_finding_validation',
]);

export function formatAutoRunMemoryEvent(event: any): string | null {
  if (event?.type === 'tool_start' && TYPED_MEMORY_TOOLS.has(event.tool_name)) {
    return `🧠 ${event.tool_name}: ${formatToolInput(event.tool_name, event.tool_input || {})}`;
  }

  if (event?.type === 'task_started' && event.task_kind === 'finding_validation') {
    const title = String(event.title || 'Finding').replace(/^Verify finding:\s*/i, '');
    return `🔎 Verifying finding "${title}"`;
  }

  if (event?.type === 'task_done' && event.finding_resolution) {
    const title = String(event.title || 'Finding').replace(/^Verify finding:\s*/i, '');
    const reason = typeof event.status_reason === 'string' && event.status_reason.trim()
      ? `: ${event.status_reason.trim()}`
      : '';
    return event.finding_resolution === 'verified'
      ? `✅ Finding verified "${title}"${reason}`
      : `⚠️ Finding requires validation "${title}"${reason}`;
  }

  return null;
}
