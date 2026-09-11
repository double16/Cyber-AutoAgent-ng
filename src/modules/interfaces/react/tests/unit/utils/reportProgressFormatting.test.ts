import {describe, expect, it} from '@jest/globals';
import {formatAutoRunReportProgress} from '../../../src/utils/reportProgressFormatting.js';

describe('formatAutoRunReportProgress', () => {
  it('marks validation failures as requiring validation', () => {
    expect(formatAutoRunReportProgress({
      report_step_index: 2,
      report_step_total: 4,
      report_step_kind: 'validation_failure',
      report_step_label: 'Requires validation: IDOR',
    })).toBe('➡️ Final report 2/4 [requires validation]: Requires validation: IDOR');
  });

  it('formats ordinary and unindexed report progress', () => {
    expect(formatAutoRunReportProgress({
      report_step_index: 1,
      report_step_total: 3,
      report_step_kind: 'finding',
      report_step_label: 'Finding: SQL injection',
    })).toBe('➡️ Final report 1/3: Finding: SQL injection');
    expect(formatAutoRunReportProgress({})).toBe('➡️ Final report report');
  });
});
