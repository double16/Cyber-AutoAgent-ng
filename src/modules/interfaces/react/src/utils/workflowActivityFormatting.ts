export const formatWorkflowActivityEvent = (event: any): string | null => {
  if (event?.type !== 'workflow_activity') return null;

  const rawLabel = String(event.label || event.action || event.activity || 'workflow').replaceAll('_', ' ');
  const label = rawLabel.charAt(0).toUpperCase() + rawLabel.slice(1);
  const status = String(event.status || 'started');
  const phase = event.phase_id != null ? ` phase ${event.phase_id}` : '';
  const task = event.task_title ? `: ${event.task_title}` : '';
  const cycle = event.cycle != null && event.cycle_total != null
    ? ` [cycle ${event.cycle}/${event.cycle_total}]`
    : '';
  const attempt = !cycle && event.attempt != null && event.attempt_total != null
    ? ` [${event.attempt}/${event.attempt_total}]`
    : '';
  const icon = status === 'completed' ? '✅' : status === 'failed' ? '⚠️' : '🔄';
  return `${icon} ${label}${phase}${task}${cycle || attempt} ${status}`;
};
