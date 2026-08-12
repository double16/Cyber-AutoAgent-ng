function formatTaskScope(event: any): string {
  const scope = typeof event?.target_scope === 'string' ? event.target_scope.trim() : '';
  const ids = Array.isArray(event?.target_ids) ? event.target_ids.filter(Boolean).join(',') : '';
  if (!scope || scope === 'all') return '';
  return ids ? ` [scope: ${ids}]` : ` [scope: ${scope}]`;
}

export function formatAutoRunMemoryEvent(event: any): string | null {
  if (event?.type === 'memory_added') {
    const category = String(event.category || 'memory').replace(/_/g, ' ');
    const preview = typeof event.content_preview === 'string' && event.content_preview.trim()
      ? `: ${event.content_preview.trim()}`
      : '';
    return `🧠 Memory added (${category})${preview}`;
  }

  if (event?.type === 'task_started' && event.task_kind === 'finding_validation') {
    const title = String(event.title || 'Finding').replace(/^Verify finding:\s*/i, '');
    return `🔎 Verifying finding "${title}"${formatTaskScope(event)}`;
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
