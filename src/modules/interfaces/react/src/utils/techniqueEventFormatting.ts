/** Compact lifecycle summaries shared by interactive and headless output. */
export const techniqueEventTypes = [
  'technique_sync_complete', 'technique_recommended', 'technique_build_progress',
  'technique_resolved', 'technique_evaluated', 'technique_deactivated', 'technique_warning',
] as const;

export type TechniqueEventType = typeof techniqueEventTypes[number];
export type TechniqueEvent = {
  type: TechniqueEventType;
  task_uid?: string;
  activity_id?: string;
  component_id?: string;
  research_round?: number;
  revision_id?: string;
  operation_id?: string;
  title?: string;
  origin?: string;
  stage?: string;
  status?: string;
  attempt?: number;
  elapsed_seconds?: number;
  fetched_count?: number;
  selected_count?: number;
  source_url?: string;
  source_decision?: 'retained' | 'rejected';
  score?: -1 | 0 | 1;
  reason?: string;
  sentiment_count?: number;
  sentiment_mean?: number;
  snapshot_path?: string;
  diagnostic?: string;
  retry_pending?: boolean;
  applicability?: 'supported' | 'conditional' | 'incompatible' | 'uncertain';
  unmet_prerequisite_count?: number;
  [key: string]: unknown;
};

const text = (value: unknown): string => typeof value === 'string'
  ? value.replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0, 1000) : '';

export const formatTechniqueEvent = (input: { type?: unknown }): string | null => {
  const event = input as { type?: unknown; [key: string]: unknown };
  const name = text(event.title) || text(event.revision_id) || 'guidance';
  let message: string;
  switch (event.type) {
    case 'technique_sync_complete':
      message = `Technique library: ${event.added ?? 0} added, ${event.updated ?? 0} updated, ${event.unchanged ?? 0} unchanged`;
      break;
    case 'technique_recommended':
      message = `Technique lookup recommended (${text(event.match_kind)}): ${text(event.title) || text(event.trigger_id)}`;
      break;
    case 'technique_build_progress':
      message = `Technique ${text(event.stage).replaceAll('_', ' ')}: ${text(event.status).replaceAll('_', ' ')}`;
      if (event.attempt != null) message += `, attempt ${event.attempt} of 3`;
      if (event.fetched_count != null) message += `, ${event.fetched_count} unique documents fetched total`;
      if (event.selected_count != null) {
        message += `, ${event.selected_count} ${event.selected_count === 1 ? 'source' : 'sources'} retained`;
      }
      if (typeof event.elapsed_seconds === 'number') message += `, ${event.elapsed_seconds.toFixed(1)}s`;
      if (event.stage === 'request_alignment' && event.original_request !== event.adjusted_request
        && typeof event.adjusted_request === 'string') {
        message += `, aligned request: ${text(event.adjusted_request)}`;
      }
      if (event.source_decision && event.source_url) {
        message += `, source ${text(event.source_decision)}: ${text(event.source_url)}`;
      }
      if (event.reason) message += ` — ${text(event.reason)}`;
      break;
    case 'technique_resolved':
      message = `Technique ${text(event.outcome)}: ${name}`;
      if (event.applicability) message += ` (${text(event.applicability)})`;
      if (typeof event.unmet_prerequisite_count === 'number' && event.unmet_prerequisite_count > 0) {
        message += `, ${event.unmet_prerequisite_count} unmet prerequisites`;
      }
      break;
    case 'technique_evaluated': {
      const label = event.score === 1 ? 'helpful (+1)' : event.score === -1 ? 'unhelpful (-1)' : 'neutral (0)';
      message = `Technique evaluated: ${name}, ${label} — ${text(event.reason)}`;
      if (typeof event.sentiment_count === 'number' && typeof event.sentiment_mean === 'number') {
        message += ` (mean ${event.sentiment_mean.toFixed(2)} across ${event.sentiment_count} uses)`;
      }
      break;
    }
    case 'technique_deactivated':
      message = `Technique deactivated: ${name}, negative feedback`;
      break;
    case 'technique_warning':
      message = `Technique warning (${text(event.stage)}): ${text(event.diagnostic)}`;
      if (event.retry_pending) message += ' (retry pending)';
      break;
    default:
      return null;
  }
  if (event.component_id) message += ` [component ${text(event.component_id)}]`;
  if (event.research_round != null) message += ` [research round ${event.research_round}]`;
  if (event.snapshot_path) message += ` [${text(event.snapshot_path)}]`;
  return event.task_uid ? `[${text(event.task_uid)}] ${message}` : message;
};

export const techniqueEventSeverity = (event: {type?: unknown; status?: unknown}): 'info' | 'warning' =>
  event.type === 'technique_warning' || event.type === 'technique_deactivated'
    || (event.type === 'technique_build_progress' && event.status === 'failed') ? 'warning' : 'info';
