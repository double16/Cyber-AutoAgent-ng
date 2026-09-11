const INSTALL_MARKER = Symbol.for('cyber-autoagent.performanceTimelineGuardInstalled');

type PerformanceLike = {
  measure?: (...args: any[]) => any;
  clearMarks?: (name?: string) => void;
  clearMeasures?: (name?: string) => void;
};

type GuardedPerformance = PerformanceLike & {
  [INSTALL_MARKER]?: boolean;
};

export const installPerformanceTimelineGuard = (target: PerformanceLike | undefined = globalThis.performance): boolean => {
  const perf = target as GuardedPerformance | undefined;
  if (process.env.CYBER_KEEP_PERFORMANCE_MEASURES === 'true') {
    return false;
  }

  if (!perf || perf[INSTALL_MARKER] || typeof perf.measure !== 'function') {
    return false;
  }

  const clearMarks = typeof perf.clearMarks === 'function'
    ? perf.clearMarks.bind(perf)
    : undefined;
  const clearMeasures = typeof perf.clearMeasures === 'function'
    ? perf.clearMeasures.bind(perf)
    : undefined;

  const clearTimeline = () => {
    try {
      clearMeasures?.();
    } catch {}
    try {
      clearMarks?.();
    } catch {}
  };

  try {
    clearTimeline();
    Object.defineProperty(perf, 'measure', {
      configurable: true,
      writable: true,
      value: () => undefined,
    });
    perf[INSTALL_MARKER] = true;
    return true;
  } catch {
    return false;
  }
};

installPerformanceTimelineGuard();
