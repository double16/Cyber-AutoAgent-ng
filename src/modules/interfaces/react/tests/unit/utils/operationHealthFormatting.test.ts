import { describe, expect, it } from '@jest/globals';

import {
  appendOperationHealth,
  formatOperationHealth,
  isPostAssessmentStage,
} from '../../../src/utils/operationHealthFormatting.js';

describe('operation health formatting', () => {
  it.each([
    ['excellent', 0.946, 95, '💚', 'green'],
    ['good', 0.82, 82, '💚', 'cyan'],
    ['degraded', 0.615, 62, '💛', 'yellow'],
    ['poor', 0.2, 20, '♥️', 'red'],
  ])('formats %s health for terminal display', (band, score, percent, emoji, color) => {
    const visual = formatOperationHealth({ status: 'available', score, band });

    expect(visual).toEqual({
      scorePercent: percent,
      band,
      emoji,
      color,
      label: `${emoji} ${percent}% ${String(band).toUpperCase()}`,
    });
  });

  it.each([
    null,
    {},
    { status: 'unavailable', score: 0.8, band: 'good' },
    { status: 'available', score: Number.NaN, band: 'good' },
    { status: 'available', score: -0.1, band: 'poor' },
    { status: 'available', score: 1.1, band: 'excellent' },
    { status: 'available', score: 0.8, band: 'unknown' },
  ])('omits invalid health snapshot %#', health => {
    expect(formatOperationHealth(health)).toBeNull();
  });

  it('appends compact health to headless progress text', () => {
    expect(appendOperationHealth(
      '➡️ Budget 42% | Duration 8m 10s',
      { status: 'available', score: 0.82, band: 'good', failure_count: 4 },
    )).toBe('➡️ Budget 42% | Duration 8m 10s | 💚 82% GOOD');
  });

  it('suppresses assessment status while the assessment is running', () => {
    expect(formatOperationHealth(
      {status: 'available', score: 0.82, band: 'good', completion_feasible: false},
    )?.label).toBe('💚 82% GOOD');
  });

  it('marks complete and incomplete assessments after assessment execution', () => {
    expect(formatOperationHealth(
      {status: 'available', score: 0.82, band: 'good', completion_feasible: false},
      true,
    )?.label).toBe('💚 82% GOOD · INCOMPLETE');
    expect(formatOperationHealth(
      {status: 'available', score: 0.82, band: 'good', completion_feasible: true},
      true,
    )?.label).toBe('💚 82% GOOD · COMPLETE');
  });

  it.each([
    ['final_report', undefined, 'progress_update'],
    ['ragas_evaluation', undefined, 'progress_update'],
    [undefined, 'FINAL REPORT', 'progress_update'],
    [undefined, undefined, 'operation_finalized'],
  ])('recognizes post-assessment stage %#', (stage, step, eventType) => {
    expect(isPostAssessmentStage(stage, step, eventType)).toBe(true);
  });

  it('leaves headless progress text unchanged when health is unavailable', () => {
    expect(appendOperationHealth('➡️ Budget 42%', { status: 'unavailable' })).toBe('➡️ Budget 42%');
  });
});
