import {afterEach, beforeEach, expect, it, jest} from '@jest/globals';
const exec = jest.fn<any>(); const stat = jest.fn<any>();
jest.unstable_mockModule('child_process', () => ({exec}));
jest.unstable_mockModule('fs/promises', () => ({stat}));
const {DeploymentDetector} = await import('../../../src/services/DeploymentDetector.js');
let commands: Record<string, string | Error>;
const oldRoot = process.env.CYBER_PROJECT_ROOT;
beforeEach(() => {
  DeploymentDetector.getInstance().clearCache();
  commands = {'python3 --version': 'Python 3.12.4', 'docker info': '', 'docker ps --format "{{.Names}}"': 'project-cyber-autoagent-1\nproject-langfuse-1\nother'};
  exec.mockImplementation((command: string, ...args: any[]) => {
    const cb = args.at(-1); const result = commands[command] ?? '';
    cb(result instanceof Error ? result : null, {stdout: result instanceof Error ? '' : result});
  });
  stat.mockRejectedValue(new Error('missing')); delete process.env.CYBER_PROJECT_ROOT;
});
afterEach(() => {if (oldRoot === undefined) delete process.env.CYBER_PROJECT_ROOT; else process.env.CYBER_PROJECT_ROOT = oldRoot; jest.restoreAllMocks();});
it('detects local imports and compose-prefixed containers, and serves cached results', async () => {
  const detector = DeploymentDetector.getInstance();
  const config = {isConfigured: true, deploymentMode: 'local-cli', hasSeenWelcome: true} as any;
  const result = await detector.detectDeployments(config);
  expect(result.message).toBe('Ready to use'); expect(result.availableDeployments.every(d => d.isHealthy)).toBe(true);
  expect(await detector.detectDeployments(config)).toBe(result);
  expect(await detector.quickValidate(config)).toBe(true);
  expect(await detector.quickValidate({} as any)).toBe(false);
  expect(await detector.quickValidate({...config, isConfigured: false})).toBe(false);
});
it('reports first run and offline deployments without losing existing configuration', async () => {
  commands['python3 --version'] = new Error('missing'); commands['python --version'] = new Error('missing'); commands['docker info'] = new Error('offline');
  const detector = DeploymentDetector.getInstance();
  const first = await detector.detectDeployments({} as any);
  expect(first.needsSetup).toBe(true); expect(first.availableDeployments.every(d => !d.isHealthy)).toBe(true);
  const configured = await detector.detectDeployments({isConfigured: true, deploymentMode: 'full-stack'} as any, {noCache: true});
  expect(configured.message).toBe('Configured but deployment offline');
});
it.each(['venv', 'old-python', 'unknown-python', 'package-failure', 'containers-failure'])('handles %s independently', async scenario => {
  if (scenario === 'venv') {
    process.env.CYBER_PROJECT_ROOT = '/project'; stat.mockImplementation(async (p: string) => {if (p === '/project/.venv') return {isDirectory: () => true}; throw new Error('missing');});
  }
  if (scenario === 'old-python') commands['python3 --version'] = 'Python 3.11';
  if (scenario === 'unknown-python') commands['python3 --version'] = 'unknown';
  if (scenario === 'containers-failure') commands['docker ps --format "{{.Names}}"'] = new Error('offline');
  if (scenario === 'package-failure') exec.mockImplementation((cmd: string, ...args: any[]) => args.at(-1)(cmd.includes('PYTHONPATH') ? new Error('missing dependency') : null, {stdout: commands[cmd] ?? ''}));
  const result = await DeploymentDetector.getInstance().detectDeployments({} as any);
  expect(result.availableDeployments).toHaveLength(3);
  expect(result.availableDeployments[0].isHealthy).toBe(scenario === 'venv' || scenario === 'containers-failure');
});
it('locates a project venv after an invalid environment override', async () => {
  process.env.CYBER_PROJECT_ROOT = '/invalid';
  stat.mockImplementation(async (p: string) => {
    if (p.endsWith('/pyproject.toml')) return {isDirectory: () => false};
    if (p === `${process.cwd()}/.venv`) return {isDirectory: () => true};
    throw new Error('missing');
  });
  const result = await DeploymentDetector.getInstance().detectDeployments({} as any);
  expect(result.availableDeployments[0].details.venvExists).toBe(true);
});
