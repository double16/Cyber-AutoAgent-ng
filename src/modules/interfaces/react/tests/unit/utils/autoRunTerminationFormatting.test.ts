import {describe, expect, it} from '@jest/globals';
import {formatAutoRunTerminationEvent} from '../../../src/utils/autoRunTerminationFormatting.js';

describe('formatAutoRunTerminationEvent', () => {
  it.each([
    ['complete', 'Assessment complete: 3 phases evaluated'],
    ['budget_limit', 'Duration budget reached'],
    ['max_tokens', 'Model token limit reached. Switching to final report.'],
    ['network_timeout', 'The model provider timed out'],
    ['error', 'Workflow invariant failed: active task is missing'],
  ])('formats the %s termination reason', (reason, message) => {
    expect(formatAutoRunTerminationEvent({
      type: 'termination_reason',
      reason,
      message,
    })).toBe(`🛑 Termination (${reason}): ${message}`);
  });

  it('preserves the backend termination message exactly', () => {
    const message = '  Workflow invariant failed: expected phase evidence.  ';

    expect(formatAutoRunTerminationEvent({
      type: 'termination_reason',
      reason: 'error',
      message,
    })).toBe(`🛑 Termination (error): ${message}`);
  });

  it('supports unknown reasons and falls back when the message is missing', () => {
    expect(formatAutoRunTerminationEvent({
      type: 'termination_reason',
      reason: 'future_reason',
    })).toBe('🛑 Termination (future_reason): Operation terminated.');
    expect(formatAutoRunTerminationEvent({
      type: 'termination_reason',
      message: 'Stopped without a reason',
    })).toBe('🛑 Termination (unknown): Stopped without a reason');
  });

  it('ignores unrelated and invalid events', () => {
    expect(formatAutoRunTerminationEvent({type: 'operation_complete'})).toBeNull();
    expect(formatAutoRunTerminationEvent(null)).toBeNull();
  });
});
