export const formatWorkflowActivityEvent = (event: any): string | null => {
  if (event?.type !== 'workflow_activity') return null;

  const label = String(event.label || event.action || event.activity || 'workflow').replaceAll('_', ' ');
  const status = String(event.status || 'started');
  const phase = event.phase_id != null ? ` phase ${event.phase_id}` : '';
  const task = event.task_title ? `: ${event.task_title}` : '';
  const attempt = event.attempt != null && event.attempt_total != null
    ? ` [${event.attempt}/${event.attempt_total}]`
    : '';
  const icon = status === 'completed' ? '✅' : status === 'failed' ? '⚠️' : '🔄';
  return `${icon} ${label}${phase}${task}${attempt} ${status}`;
};
