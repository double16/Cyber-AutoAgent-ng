export type OperationHealthBand = 'excellent' | 'good' | 'degraded' | 'poor';

export interface OperationHealthSnapshot {
  health_version?: string;
  status?: string;
  score?: number;
  band?: string;
  [key: string]: unknown;
}

export interface OperationHealthVisual {
  scorePercent: number;
  band: OperationHealthBand;
  emoji: string;
  color: string;
  label: string;
}

const BAND_VISUALS: Record<OperationHealthBand, { emoji: string; color: string }> = {
  excellent: { emoji: '💚', color: 'green' },
  good: { emoji: '💚', color: 'cyan' },
  degraded: { emoji: '💛', color: 'yellow' },
  poor: { emoji: '♥️', color: 'red' },
};

const POST_ASSESSMENT_OPERATION_STAGES = new Set(['final_report', 'ragas_evaluation']);
const POST_ASSESSMENT_EVENT_TYPES = new Set([
  'assessment_complete',
  'operation_complete',
  'operation_finalized',
]);

export const isPostAssessmentStage = (
  operationStage: unknown,
  step?: unknown,
  eventType?: unknown,
): boolean => (
  POST_ASSESSMENT_OPERATION_STAGES.has(String(operationStage ?? ''))
  || String(step ?? '').toUpperCase() === 'FINAL REPORT'
  || POST_ASSESSMENT_EVENT_TYPES.has(String(eventType ?? ''))
);

export const formatOperationHealth = (
  health: unknown,
  showAssessmentStatus = false,
): OperationHealthVisual | null => {
  if (!health || typeof health !== 'object') return null;
  const snapshot = health as OperationHealthSnapshot;
  if (snapshot.status && snapshot.status !== 'available') return null;
  if (typeof snapshot.score !== 'number' || !Number.isFinite(snapshot.score)) return null;
  if (snapshot.score < 0 || snapshot.score > 1) return null;

  const band = String(snapshot.band || '').toLowerCase() as OperationHealthBand;
  const visual = BAND_VISUALS[band];
  if (!visual) return null;

  const scorePercent = Math.round(snapshot.score * 100);
  const assessmentStatus = showAssessmentStatus
    ? snapshot.completion_feasible === false
      ? ' · INCOMPLETE'
      : ' · COMPLETE'
    : '';
  return {
    scorePercent,
    band,
    emoji: visual.emoji,
    color: visual.color,
    label: `${visual.emoji} ${scorePercent}% ${band.toUpperCase()}${assessmentStatus}`,
  };
};

export const appendOperationHealth = (
  message: string,
  health: unknown,
  showAssessmentStatus = false,
): string => {
  const visual = formatOperationHealth(health, showAssessmentStatus);
  return visual ? `${message} | ${visual.label}` : message;
};
