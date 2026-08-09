export const formatAutoRunEvaluationEvent = (event: any): string | null => {
  if (event?.type === 'progress_update' && event.operation_stage === 'ragas_evaluation') {
    const evaluationIndex = Number(event.evaluation_step_index);
    const evaluationTotal = Number(event.evaluation_step_total);
    const evaluationLabel = typeof event.evaluation_step_label === 'string'
      ? event.evaluation_step_label
      : '';
    const isMetric = event.evaluation_step_kind === 'metric';
    const progressLabel = isMetric && Number.isFinite(evaluationIndex) && Number.isFinite(evaluationTotal)
      ? `${evaluationIndex}/${evaluationTotal}`
      : (isMetric ? 'metric' : 'preparation');
    return `➡️ Ragas evaluation ${progressLabel}${evaluationLabel ? `: ${evaluationLabel}` : ''}`;
  }

  if (event?.type === 'evaluation_step_complete') {
    const status = String(event.status || 'completed');
    if (status === 'completed') return null;
    const scope = String(event.evaluation_scope || 'operation');
    const name = String(event.evaluation_metric || event.evaluation_step_kind || 'evaluation')
      .replace(/_/g, ' ');
    const message = typeof event.message === 'string' && event.message ? `: ${event.message}` : '';
    return `${status === 'failed' ? '❌' : '⚠️'} Ragas evaluation ${scope}: ${name} ${status}${message}`;
  }

  if (event?.type === 'evaluation_complete') {
    const status = String(event.status || (event.success === false ? 'failed' : 'completed'));
    if (status !== 'completed') {
      const label = status === 'no_results' ? 'completed without results' : 'failed';
      const message = typeof event.message === 'string' && event.message ? `: ${event.message}` : '';
      return `${status === 'failed' ? '❌' : '⚠️'} Evaluation ${label}${message}`;
    }

    const scores = event.scores && typeof event.scores === 'object'
      ? Object.entries(event.scores).filter(([, value]) => typeof value === 'number') as Array<[string, number]>
      : [];
    const average = typeof event.average_score === 'number'
      ? event.average_score
      : (scores.length > 0 ? scores.reduce((sum, [, value]) => sum + value, 0) / scores.length : null);
    const metricCount = Number.isFinite(Number(event.metrics_evaluated))
      ? Number(event.metrics_evaluated)
      : scores.length;
    const averageText = average == null ? '' : ` | Average ${(average * 100).toFixed(1)}%`;
    const scoreText = scores.length === 0
      ? ''
      : ` | ${scores.map(([name, value]) => `${name}=${(value * 100).toFixed(1)}%`).join(', ')}`;
    return `✅ Evaluation complete: ${metricCount} metrics${averageText}${scoreText}`;
  }

  return null;
};
