import React from 'react';
import {EventEmitter} from 'events';
import {promisify} from 'util';
import {afterEach, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let setStep: any; let setStatus: any; let setContinue: any; let stateIndex = 0;
// The retained fallback flow has no route from the welcome screen. Capture only its step setter
// so the fallback can be exercised without replacing React state updates or effects.
jest.unstable_mockModule('react', () => ({...React, default: React, useState: (initial: any) => {
  if (initial === 'welcome') stateIndex = 0;
  const index = stateIndex++; const state = React.useState(initial);
  if (index === 0) setStep = state[1]; if (index === 3) setStatus = state[1]; if (index === 6) setContinue = state[1]; return state;
}}));
const execute = jest.fn<any>(); const exec = jest.fn(); (exec as any)[promisify.custom] = execute;
jest.unstable_mockModule('child_process', () => ({exec}));
jest.unstable_mockModule('fs', () => ({existsSync: () => true}));
const config = {isConfigured: false};
jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({useConfig: () => ({config, updateConfig: jest.fn(), saveConfig: async () => {}})}));
const manager = new EventEmitter() as any; manager.switchToMode = jest.fn<any>();
jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({ContainerManager: {getInstance: () => manager}}));
const checkHealth = jest.fn<any>();
jest.unstable_mockModule('../../../src/services/HealthMonitor.js', () => ({HealthMonitor: {getInstance: () => ({checkHealth})}}));
jest.unstable_mockModule('ink-spinner', () => ({default: () => <span>waiting</span>}));
const {InitializationFlow} = await import('../../../src/components/InitializationFlow.js');
afterEach(() => {jest.useRealTimers(); jest.clearAllMocks();});
it.each(['existing', 'new', 'start-fallback', 'failure'])('recovers %s containers through the retained fallback flow', async scenario => {
  jest.useFakeTimers(); const onComplete = jest.fn();
  execute.mockImplementation(async (cmd: string) => {
    if (scenario === 'failure' && cmd.includes('docker-compose')) throw new Error('permission denied');
    if (scenario === 'start-fallback' && cmd.endsWith(' start')) throw new Error('start failed');
    return {stdout: cmd.includes('docker ps -a') && scenario !== 'new' ? 'cyber-agent' : ''};
  });
  checkHealth.mockResolvedValue({overall: 'unhealthy', dockerRunning: true, services: [{name: 'cyber-langfuse', status: 'stopped'}]});
  let view!: ReturnType<typeof TestRenderer.create>;
  await act(async () => {view = TestRenderer.create(<InitializationFlow onComplete={onComplete}/>);});
  // setStep is assigned by the committed initial render.
  await act(async () => {setStep('checking-docker'); await jest.advanceTimersByTimeAsync(5100);});
  if (scenario === 'failure') expect(document.body.textContent).toContain('Failed to start containers');
  else expect(execute).toHaveBeenCalledWith(expect.stringContaining('docker-compose'));
  act(() => view.unmount());
});

it('renders fallback checking, starting and unknown status icons and accepts continue', async () => {
  const onComplete = jest.fn(); let view!: ReturnType<typeof TestRenderer.create>;
  act(() => {view = TestRenderer.create(<InitializationFlow onComplete={onComplete}/>);});
  act(() => {setStep('checking-config'); setContinue(true); setStatus({docker: 'success', containers: 'success', config: 'checking'});});
  expect(document.body.textContent).toContain('Configuration');
  act(() => setStatus({docker: 'starting', containers: 'pending', config: 'pending'}));
  act(() => setStatus({docker: 'unknown', containers: 'pending', config: 'pending'}));
  act(() => (globalThis as any).__inkInputHandler('', {return: true}));
  expect(onComplete).toHaveBeenCalled(); act(() => view.unmount());
});
