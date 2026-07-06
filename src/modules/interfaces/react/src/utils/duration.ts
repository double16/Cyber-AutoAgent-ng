export function parseDurationSeconds(duration: string): number | null {
  const matches = [...duration.matchAll(/(\d+)\s*([hms])/g)];
  if (matches.length === 0) {
    return null;
  }

  const normalizedDuration = duration.replace(/\s+/g, '');
  const consumedDuration = matches.map((match) => `${match[1]}${match[2]}`).join('');
  if (normalizedDuration !== consumedDuration) {
    return null;
  }

  return matches.reduce((totalSeconds, match) => {
    const value = Number(match[1]);
    const unit = match[2];

    if (unit === 'h') return totalSeconds + (value * 3600);
    if (unit === 'm') return totalSeconds + (value * 60);
    return totalSeconds + value;
  }, 0);
}

export function estimateEtaSeconds(duration: string | undefined, progressPercent: number | undefined): number | null {
  if (
    !duration
    || duration === '0s'
    || progressPercent === undefined
    || !Number.isFinite(progressPercent)
    || progressPercent <= 0
    || progressPercent >= 100
  ) {
    return null;
  }

  const elapsedSeconds = parseDurationSeconds(duration);
  if (elapsedSeconds === null) {
    return null;
  }

  return Math.round(elapsedSeconds / (progressPercent / 100));
}
