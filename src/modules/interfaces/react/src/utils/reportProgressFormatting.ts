export function formatAutoRunReportProgress(event: any): string {
  const reportIndex = Number(event?.report_step_index);
  const reportTotal = Number(event?.report_step_total);
  const reportLabel = typeof event?.report_step_label === 'string' ? event.report_step_label : '';
  const progressLabel = Number.isFinite(reportIndex) && Number.isFinite(reportTotal)
    ? `${reportIndex}/${reportTotal}`
    : 'report';
  const kindLabel = event?.report_step_kind === 'validation_failure' ? ' [requires validation]' : '';
  return `➡️ Final report ${progressLabel}${kindLabel}${reportLabel ? `: ${reportLabel}` : ''}`;
}
