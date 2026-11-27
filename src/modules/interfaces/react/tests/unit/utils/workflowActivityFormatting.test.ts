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
    ['completed', '✅ Plan review completed'],
    ['failed', '⚠️ Plan review failed'],
    ['started', '🔄 Plan review started'],
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
    })).toBe('🔄 Phase evaluator started');
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      activity: 'task_creation',
    })).toBe('🔄 Task creation started');
  });

  it('uppercases the first character of a lower-case action', () => {
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      action: 'task_create_prompt',
    })).toBe('🔄 Task create prompt started');
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

  it.each([
    ['duplicate_submission', 'repeated rejected submission'],
    ['attempts_exhausted', 'attempts exhausted'],
  ])('shows the task creation failure and %s stop reason', (stopReason, label) => {
    expect(formatWorkflowActivityEvent({
      type: 'workflow_activity',
      label: 'Task creation',
      phase_id: 4,
      status: 'failed',
      failure_summary: 'tasks.0.task_role: invalid role',
      stop_reason: stopReason,
      rejected_response: 'must never be displayed',
    })).toBe(`⚠️ Task creation phase 4 failed: tasks.0.task_role: invalid role (${label})`);
  });

  it('bounds diagnostics to one line and omits them from successful events', () => {
    const event = {
      type: 'workflow_activity', label: 'Task creation', failure_summary: 'first\nsecond\rthird ' + 'x'.repeat(2000),
    };
    expect(formatWorkflowActivityEvent({...event, status: 'completed'})).toBe('✅ Task creation completed');
    const failure = formatWorkflowActivityEvent({...event, status: 'failed'});
    expect(failure).toContain('first second third');
    expect(failure?.length).toBeLessThan(1100);
  });
});
