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
  excellent: { emoji: '🟢', color: 'green' },
  good: { emoji: '🟢', color: 'cyan' },
  degraded: { emoji: '🟡', color: 'yellow' },
  poor: { emoji: '🔴', color: 'red' },
};

export const formatOperationHealth = (health: unknown): OperationHealthVisual | null => {
  if (!health || typeof health !== 'object') return null;
  const snapshot = health as OperationHealthSnapshot;
  if (snapshot.status && snapshot.status !== 'available') return null;
  if (typeof snapshot.score !== 'number' || !Number.isFinite(snapshot.score)) return null;
  if (snapshot.score < 0 || snapshot.score > 1) return null;

  const band = String(snapshot.band || '').toLowerCase() as OperationHealthBand;
  const visual = BAND_VISUALS[band];
  if (!visual) return null;

  const scorePercent = Math.round(snapshot.score * 100);
  return {
    scorePercent,
    band,
    emoji: visual.emoji,
    color: visual.color,
    label: `🤍${visual.emoji} ${scorePercent}% ${band.toUpperCase()}`,
  };
};

export const appendOperationHealth = (message: string, health: unknown): string => {
  const visual = formatOperationHealth(health);
  return visual ? `${message} | ${visual.label}` : message;
};
