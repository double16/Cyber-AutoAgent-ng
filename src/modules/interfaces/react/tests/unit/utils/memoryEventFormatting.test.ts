import {describe, expect, it} from '@jest/globals';
import {formatAutoRunMemoryEvent} from '../../../src/utils/memoryEventFormatting.js';

describe('formatAutoRunMemoryEvent', () => {
  it('formats typed memory tool actions', () => {
    expect(formatAutoRunMemoryEvent({
      type: 'tool_start',
      tool_name: 'store_finding',
      tool_input: {title: 'SQL injection', severity: 'HIGH', target: '/search'},
    })).toContain('submitting finding for verification');
  });

  it('formats finding verification lifecycle events', () => {
    expect(formatAutoRunMemoryEvent({
      type: 'task_started',
      task_kind: 'finding_validation',
      title: 'Verify finding: IDOR',
    })).toBe('🔎 Verifying finding "IDOR"');
    expect(formatAutoRunMemoryEvent({
      type: 'task_done',
      title: 'Verify finding: IDOR',
      finding_resolution: 'verified',
      status_reason: 'Evidence approved',
    })).toBe('✅ Finding verified "IDOR": Evidence approved');
    expect(formatAutoRunMemoryEvent({
      type: 'task_done',
      title: 'Verify finding: IDOR',
      finding_resolution: 'validation_failure',
      status_reason: 'Control missing',
    })).toBe('⚠️ Finding requires validation "IDOR": Control missing');
  });

  it('ignores unrelated events', () => {
    expect(formatAutoRunMemoryEvent({type: 'tool_start', tool_name: 'shell'})).toBeNull();
    expect(formatAutoRunMemoryEvent({type: 'task_done', title: 'Recon'})).toBeNull();
  });
});
