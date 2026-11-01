import {promisify} from 'util';
import {afterEach, beforeEach, expect, it, jest} from '@jest/globals';
let byImage = false; let missing = new Set<string>(); let states: Record<string, string>; let failure: any;
const exec = jest.fn<any>();
(exec as any)[promisify.custom] = async (cmd: string) => {
  if (failure) throw failure;
  if (cmd === 'docker info') return {stdout: ''};
  const name = Object.keys(states).sort((a, b) => b.length - a.length).find(n => cmd.includes(n));
  if (byImage && cmd.includes('name=^cyber-autoagent$')) return {stdout: ''};
  if (cmd.includes('ancestor=')) return {stdout: missing.has('cyber-autoagent') ? '' : 'agent-id'};
  if (cmd.includes('docker inspect')) return {stdout: states[name ?? 'cyber-autoagent']};
  return {stdout: name && !missing.has(name) ? name : ''};
};
jest.unstable_mockModule('child_process', () => ({exec}));
jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({ContainerManager: {getInstance: () => ({getCurrentMode: async () => 'full-stack', getDeploymentConfig: () => ({services: ['cyber-autoagent', 'langfuse-web', 'redis']})})}}));
const {HealthMonitor} = await import('../../../src/services/HealthMonitor.js');
beforeEach(() => {jest.useFakeTimers(); byImage = false; missing = new Set(); failure = undefined; states = {'cyber-autoagent': 'running|no-health|<no value>', 'cyber-langfuse': 'running||', 'cyber-langfuse-redis': 'running|healthy|2026-01-01T00:00:00Z'};});
afterEach(() => {HealthMonitor.getInstance().stopMonitoring(); jest.useRealTimers();});
it('reports healthy and degraded stacks, missing services and actionable recommendations', async () => {
  const monitor = HealthMonitor.getInstance();
  expect((await monitor.checkHealth()).overall).toBe('healthy');
  states['cyber-langfuse-redis'] = 'exited|no-health|';
  expect((await monitor.checkHealth()).overall).toBe('degraded');
  missing.add('cyber-autoagent'); missing.add('cyber-langfuse');
  const detail = await monitor.getDetailedHealth();
  expect(detail.recommendations).toContain('Run "docker-compose up -d" to start the agent');
  expect(detail.recommendations).toContain('Langfuse is stopped. Observability features will be unavailable');
});
it('uses image discovery when the agent exact-name probe fails', async () => {
  byImage = true;
  const monitor = HealthMonitor.getInstance();
  expect((await monitor.checkHealth()).services[0]).toMatchObject({status: 'running', health: undefined});
  monitor.startMonitoring(1000); monitor.startMonitoring(1000);
  await jest.advanceTimersByTimeAsync(1000);
  monitor.pauseMonitoring(); monitor.pauseMonitoring(); monitor.resumeMonitoring(); monitor.resumeMonitoring();
  await jest.advanceTimersByTimeAsync(30000);
  expect(monitor.getCurrentStatus()?.services).toHaveLength(3);
});
