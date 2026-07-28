import {describe, expect, it} from '@jest/globals';
import {formatWorkflowActivityEvent} from '../../../src/utils/workflowActivityFormatting.js';

describe('formatWorkflowActivityEvent', () => {
  it('prefers the concise label and includes context', () => {
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      label: 'Task prompt building',
      action: 'task_prompt_builder',
      status: 'started',
      phase_id: 2,
      task_title: 'Assess endpoint /login',
      attempt: 1,
      attempt_total: 2,
    })).toBe('🔄 Task prompt building phase 2: Assess endpoint /login [1/2] started');
  });

  it.each([
    ['completed', '✅ plan review completed'],
    ['failed', '⚠️ plan review failed'],
    ['started', '🔄 plan review started'],
  ])('formats %s status', (status, expected) => {
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      action: 'plan_review',
      status,
    })).toBe(expected);
  });

  it('falls back from label to action and activity', () => {
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      action: 'phase_evaluator',
    })).toBe('🔄 phase evaluator started');
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      activity: 'task_creation',
    })).toBe('🔄 task creation started');
  });

  it('prefers actor/critic cycle counters over JSON retry attempts', () => {
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      label: 'Plan review',
      status: 'started',
      cycle: 2,
      cycle_total: 3,
      attempt: 1,
      attempt_total: 2,
    })).toBe('🔄 Plan review [cycle 2/3] started');
  });

  it('ignores non-workflow events and does not include prompt content', () => {
    expect(formatWorkflowActivityEvent({type: 'reasoning', content: 'secret prompt'})).toBeNull();
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      label: 'Plan creation',
      prompt: 'secret prompt content',
    })).toBe('🔄 Plan creation started');
  });
});
