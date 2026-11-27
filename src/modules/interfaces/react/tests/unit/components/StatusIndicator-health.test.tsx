import React from 'react';
import {afterEach, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let notify: (value: any) => void;
const unsubscribe = jest.fn(); const stopMonitoring = jest.fn(); const mode = jest.fn<any>();
jest.unstable_mockModule('../../../src/services/HealthMonitor.js', () => ({HealthMonitor: {getInstance: () => ({subscribe: (cb: any) => {notify = cb; return unsubscribe;}, checkHealth: jest.fn(), stopMonitoring})}}));
jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({ContainerManager: {getInstance: () => ({getCurrentMode: mode})}}));
const {StatusIndicator} = await import('../../../src/components/StatusIndicator.js');
afterEach(() => {jest.useRealTimers(); jest.restoreAllMocks();});
it('deduplicates health updates, renders all health states and releases subscription', async () => {
  jest.useFakeTimers();
  mode.mockResolvedValue('single-container');
  let view!: ReturnType<typeof TestRenderer.create>;
  await act(async () => {view = TestRenderer.create(<StatusIndicator/>);});
  expect(view.toJSON()).toBeNull();
  const service = {name: 'db', displayName: 'Database', status: 'running', health: 'healthy', uptime: '1h'};
  const status = {overall: 'healthy', dockerRunning: true, services: [service]};
  act(() => notify(status));
  expect(document.body.textContent).toContain('Database');
  act(() => notify({...status}));
  act(() => notify({...status, dockerRunning: false}));
  act(() => notify({...status, services: []}));
  act(() => notify({...status, services: [{...service, status: 'stopped'}]}));
  act(() => notify(status));
  act(() => {jest.advanceTimersByTime(16000); notify(status);});
  for (const overall of ['degraded', 'unhealthy', 'unknown']) {
    act(() => notify({...status, overall}));
    expect(document.body.textContent).toContain(overall.toUpperCase());
  }
  for (const deploymentMode of ['cli', 'agent', 'full-stack', 'custom']) {
    await act(async () => view.update(<StatusIndicator compact deploymentMode={deploymentMode}/>));
    act(() => notify({...status, overall: 'degraded', dockerRunning: false}));
    expect(document.body.textContent).toContain(deploymentMode);
  }
  act(() => view.unmount());
  expect(unsubscribe).toHaveBeenCalled(); expect(stopMonitoring).toHaveBeenCalled();
});
it.each(['local-cli', 'full-stack', 'error'])('detects %s mode with safe fallback', async value => {
  jest.useFakeTimers();
  jest.spyOn(console, 'error').mockImplementation(() => {});
  if (value === 'error') mode.mockRejectedValue(new Error('offline')); else mode.mockResolvedValue(value);
  let view!: ReturnType<typeof TestRenderer.create>;
  await act(async () => {view = TestRenderer.create(<StatusIndicator compact/>);});
  act(() => notify({overall: 'unhealthy', dockerRunning: true, services: []}));
  expect(document.body.textContent).toContain(value === 'full-stack' ? 'full-stack' : 'cli');
  act(() => view.unmount());
});
