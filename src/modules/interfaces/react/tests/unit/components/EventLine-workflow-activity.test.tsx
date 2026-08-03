import React from 'react';
import {describe, expect, it, jest} from '@jest/globals';
import {TextDecoder, TextEncoder} from 'node:util';

Object.assign(globalThis, {TextDecoder, TextEncoder});

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
  useConfig: () => ({config: {modelProvider: 'bedrock', awsRegion: 'us-east-1'}}),
}));

const load = async () => {
  const mod: any = await import('../../../src/components/StreamDisplay.tsx');
  const {render} = await import('ink-testing-library');
  return {EventLine: mod.EventLine, render};
};

describe('EventLine workflow activity rendering', () => {
  it('renders label, status, phase, task, and attempt context', async () => {
    const {EventLine, render} = await load();
    const frame = render(React.createElement(EventLine, {
      event: {
        type: 'workflow_activity',
        label: 'Task prompt building',
        action: 'task_prompt_builder',
        status: 'started',
        phase_id: 2,
        task_title: 'Assess endpoint /login',
        attempt: 1,
        attempt_total: 2,
      },
      animationsEnabled: false,
    })).lastFrame() || '';

    expect(frame).toContain('Task prompt building');
    expect(frame).toContain('phase 2');
    expect(frame).toContain('Assess endpoint /login');
    expect(frame).toContain('[1/2] started');
  });

  it('prefers label and falls back to action/activity', async () => {
    const {EventLine, render} = await load();
    const withLabel = render(React.createElement(EventLine, {
      event: {type: 'workflow_activity', label: 'Plan creation', action: 'wrong_action'},
      animationsEnabled: false,
    })).lastFrame() || '';
    const withAction = render(React.createElement(EventLine, {
      event: {type: 'workflow_activity', action: 'phase_evaluator'},
      animationsEnabled: false,
    })).lastFrame() || '';

    expect(withLabel).toContain('Plan creation');
    expect(withLabel).not.toContain('wrong action');
    expect(withAction).toContain('Phase evaluator');
    expect(withAction).toContain('🔄');
  });

  it('renders completed and failed statuses without prompt contents', async () => {
    const {EventLine, render} = await load();
    const frames = ['completed', 'failed'].map(status => render(React.createElement(EventLine, {
      event: {type: 'workflow_activity', label: 'Plan review', status, prompt: 'secret prompt'},
      animationsEnabled: false,
    })).lastFrame() || '');

    expect(frames[0]).toContain('completed');
    expect(frames[1]).toContain('failed');
    expect(frames[0]).toContain('✅');
    expect(frames[1]).toContain('⚠️');
    expect(frames.join('\n')).not.toContain('secret prompt');
  });
});
