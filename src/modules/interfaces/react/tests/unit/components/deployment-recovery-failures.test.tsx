import React from 'react';
import {promisify} from 'util';
import {afterEach, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const execute = jest.fn<any>(); const exec = jest.fn<any>(); (exec as any)[promisify.custom] = execute;
jest.unstable_mockModule('child_process', () => ({exec}));
jest.unstable_mockModule('ink-spinner', () => ({default: () => <span>waiting</span>}));
const updateConfig = jest.fn(); const saveConfig = jest.fn<any>();
jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({useConfig: () => ({updateConfig, saveConfig})}));
const {DeploymentRecovery} = await import('../../../src/components/DeploymentRecovery.js');
const originalFetch = globalThis.fetch;
afterEach(() => {globalThis.fetch = originalFetch; jest.useRealTimers(); jest.clearAllMocks();});
it.each(['stopped', 'already-running', 'unhealthy', 'failure'])('recovers Docker state %s', async scenario => {
  jest.useFakeTimers(); const onComplete = jest.fn(); const skip = jest.fn();
  execute.mockImplementation(async (cmd: string) => {
    if (scenario === 'failure') throw 'failure';
    if (cmd.includes('docker ps --filter')) return {stdout: scenario === 'already-running' ? 'cyber-autoagent' : ''};
    return {stdout: scenario === 'stopped' ? 'Exited (1)' : 'Up (unhealthy)'};
  });
  let view!: ReturnType<typeof TestRenderer.create>;
  act(() => {view = TestRenderer.create(<DeploymentRecovery deployment={{mode: 'single-container', details: {}} as any} onComplete={onComplete} onSkip={skip}/>);});
  await act(async () => {(globalThis as any).__inkInputHandler('y', {}); await jest.advanceTimersByTimeAsync(5000);});
  if (scenario === 'failure') expect(document.body.textContent).toContain('Failed to recover Docker container');
  else expect(onComplete).toHaveBeenCalledWith(true);
  act(() => view.unmount());
});
it('retries failed Langfuse health probes and surfaces exhausted retries', async () => {
  jest.useFakeTimers(); execute.mockResolvedValue({stdout: ''});
  globalThis.fetch = jest.fn<any>().mockRejectedValue(new Error('offline'));
  let view!: ReturnType<typeof TestRenderer.create>;
  act(() => {view = TestRenderer.create(<DeploymentRecovery deployment={{mode: 'full-stack', details: {}} as any} onComplete={jest.fn()} onSkip={jest.fn()}/>);});
  await act(async () => {(globalThis as any).__inkInputHandler('y', {}); await jest.advanceTimersByTimeAsync(25000);});
  expect(globalThis.fetch).toHaveBeenCalledTimes(10);
  expect(document.body.textContent).toContain('Langfuse service not responding after recovery');
  act(() => view.unmount());
});
it('reports non-Error recovery failures and accepts all skip shortcuts', async () => {
  execute.mockRejectedValue('failure');
  const skip = jest.fn(); let view!: ReturnType<typeof TestRenderer.create>;
  act(() => {view = TestRenderer.create(<DeploymentRecovery deployment={{mode: 'local-cli', details: {}} as any} onComplete={jest.fn()} onSkip={skip}/>);});
  await act(async () => {(globalThis as any).__inkInputHandler('y', {});});
  expect(document.body.textContent).toContain('Recovery failed');
  for (const key of ['n', 'N', 's', 'S']) act(() => (globalThis as any).__inkInputHandler(key, {}));
  expect(skip).toHaveBeenCalledTimes(4); act(() => view.unmount());
});
