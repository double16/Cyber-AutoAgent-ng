import { EventEmitter } from 'events';
import { jest } from '@jest/globals';

let execResponses: Array<{ match: string | RegExp; stdout?: string; error?: Error }> = [];
const execMock = jest.fn((command: string, optionsOrCallback: any, maybeCallback?: any) => {
  const callback = typeof optionsOrCallback === 'function' ? optionsOrCallback : maybeCallback;
  // Broad command fallbacks keep the tests stable across small shell-format changes.
  if (command.includes('docker ps') && command.includes('.Names') && !command.includes('--filter')) {
    const response = execResponses.find(item => item.match === 'docker ps names');
    callback(null, response?.stdout ?? '', '');
    return;
  }
  if (command.includes('docker ps') && command.includes('--filter') && command.includes('name=')) {
    if (command.includes('cyber-langfuse-postgres')) {
      callback(null, '', '');
      return;
    }
    if (command.includes('cyber-langfuse-redis')) {
      callback(null, 'cyber-langfuse-redis\n', '');
      return;
    }
    if (command.includes('cyber-langfuse')) {
      callback(null, 'cyber-langfuse\n', '');
      return;
    }
    if (command.includes('cyber-autoagent')) {
      callback(null, 'cyber-autoagent\n', '');
      return;
    }
  }
  if (command.includes('docker inspect')) {
    const startedAt = new Date(Date.now() - 65_000).toISOString();
    if (command.includes('cyber-langfuse-redis')) {
      callback(null, `exited|no-health|${startedAt}\n`, '');
      return;
    }
    if (command.includes('cyber-langfuse')) {
      callback(null, `running|unhealthy|${startedAt}\n`, '');
      return;
    }
    if (command.includes('cyber-autoagent')) {
      callback(null, `running|healthy|${startedAt}\n`, '');
      return;
    }
  }
  const response = execResponses.find(item =>
    typeof item.match === 'string' ? command.includes(item.match) : item.match.test(command)
  );
  if (response?.error) {
    callback(response.error, '', response.error.message);
    return;
  }
  callback(null, response?.stdout ?? '', '');
});

let existingDirs = new Set<string>();
const statMock = jest.fn(async (target: string) => {
  if (!existingDirs.has(target)) throw new Error(`missing ${target}`);
  return { isDirectory: () => target.endsWith('.venv') || target.includes('modules') };
});

let currentMode = 'full-stack';
const containerManager = new EventEmitter() as any;
containerManager.getCurrentMode = jest.fn(async () => currentMode);
containerManager.getDeploymentConfig = jest.fn((mode: string) => ({
  mode,
  services: mode === 'full-stack'
    ? ['cyber-autoagent', 'langfuse-web', 'postgres', 'redis']
    : mode === 'single-container'
      ? ['cyber-autoagent']
      : [],
}));

jest.unstable_mockModule('child_process', () => ({
  exec: execMock,
  spawn: jest.fn(),
}));

jest.unstable_mockModule('fs/promises', () => ({
  stat: statMock,
}));

jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({
  ContainerManager: {
    getInstance: () => containerManager,
  },
}));

const loadDetector = async () => import('../../../src/services/DeploymentDetector.js');
const loadHealth = async () => import('../../../src/services/HealthMonitor.js');

describe('deployment and health services', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-06-17T12:00:00Z'));
    execResponses = [];
    existingDirs = new Set();
    currentMode = 'full-stack';
    execMock.mockClear();
    statMock.mockClear();
    containerManager.getCurrentMode.mockClear();
    containerManager.getDeploymentConfig.mockClear();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('detects healthy local, single-container, and full-stack deployments with cache', async () => {
    const { DeploymentDetector } = await loadDetector();
    const detector = new DeploymentDetector();
    existingDirs.add(`${process.cwd()}/.venv`);
    execResponses = [
      { match: 'python3 --version', stdout: 'Python 3.11.8\n' },
      { match: 'docker info', stdout: 'ok' },
      { match: 'docker ps names', stdout: 'cyber-autoagent\ncyber-langfuse\ncyber-langfuse-postgres\n' },
    ];

    const result = await detector.detectDeployments({ isConfigured: true, hasSeenWelcome: true, deploymentMode: 'full-stack' } as any);
    expect(result.needsSetup).toBe(false);
    expect(result.message).toBe('Ready to use');
    expect(result.availableDeployments).toEqual(expect.arrayContaining([
      expect.objectContaining({ mode: 'local-cli', isHealthy: true }),
      expect.objectContaining({ mode: 'single-container' }),
      expect.objectContaining({ mode: 'full-stack' }),
    ]));

    const callCount = execMock.mock.calls.length;
    const cached = await detector.detectDeployments({ isConfigured: true, hasSeenWelcome: true, deploymentMode: 'full-stack' } as any);
    expect(cached).toBe(result);
    expect(execMock).toHaveBeenCalledTimes(callCount);

    expect(await detector.quickValidate({ isConfigured: true, hasSeenWelcome: true, deploymentMode: 'local-cli' } as any)).toBe(true);
    expect(await detector.quickValidate({ isConfigured: false, hasSeenWelcome: false } as any)).toBe(false);
  });

  it('detects first-time setup, offline Docker, and import fallback failures', async () => {
    const { DeploymentDetector } = await loadDetector();
    const detector = new DeploymentDetector();
    detector.clearCache();
    execResponses = [
      { match: 'python3 --version', stdout: 'Python 3.10.1\n' },
      { match: 'import cyberautoagent', error: new Error('missing package') },
      { match: 'docker info', error: new Error('docker down') },
    ];

    const result = await detector.detectDeployments({ isConfigured: false } as any, { noCache: true });
    expect(result.needsSetup).toBe(true);
    expect(result.message).toBe('First-time setup required');
    expect(result.availableDeployments.find(d => d.mode === 'local-cli')?.isHealthy).toBe(false);
    expect(result.availableDeployments.find(d => d.mode === 'single-container')?.isHealthy).toBe(false);
    expect(result.availableDeployments.find(d => d.mode === 'full-stack')?.isHealthy).toBe(false);
  });

  it('checks health, notifies subscribers, formats service states, and recommends fixes', async () => {
    const { HealthMonitor } = await loadHealth();
    const monitor = HealthMonitor.getInstance();
    monitor.stopMonitoring();
    currentMode = 'full-stack';
    const startedAt = new Date(Date.now() - 65_000).toISOString();
    execResponses = [
      { match: 'docker info', stdout: 'ok' },
      { match: /cyber-autoagent.*Names|name=.*cyber-autoagent/, stdout: 'cyber-autoagent\n' },
      { match: /docker inspect cyber-autoagent/, stdout: `running|healthy|${startedAt}\n` },
      { match: /cyber-langfuse.*Names|name=.*cyber-langfuse/, stdout: 'cyber-langfuse\n' },
      { match: /docker inspect cyber-langfuse/, stdout: `running|unhealthy|${startedAt}\n` },
      { match: /cyber-langfuse-postgres.*Names|name=.*cyber-langfuse-postgres/, stdout: '' },
      { match: 'ancestor=cyber-autoagent:latest', stdout: '' },
      { match: /cyber-langfuse-redis.*Names|name=.*cyber-langfuse-redis/, stdout: 'cyber-langfuse-redis\n' },
      { match: /docker inspect cyber-langfuse-redis/, stdout: `exited|no-health|${startedAt}\n` },
    ];

    const listener = jest.fn();
    const unsubscribe = monitor.subscribe(listener);
    const status = await monitor.checkHealth();

    expect(status.dockerRunning).toBe(true);
    expect(status.overall).toBe('unhealthy');
    expect(status.services).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: 'cyber-autoagent' }),
      expect.objectContaining({ name: 'cyber-langfuse' }),
      expect.objectContaining({ name: 'cyber-langfuse-postgres', status: 'stopped' }),
    ]));
    expect(listener).toHaveBeenCalledWith(status);
    expect(monitor.getCurrentStatus()).toBe(status);

    const detailed = await monitor.getDetailedHealth();
    expect(detailed.recommendations.length).toBeGreaterThan(0);

    unsubscribe();
    monitor.startMonitoring(1000);
    expect(execMock).toHaveBeenCalled();
    monitor.pauseMonitoring();
    monitor.resumeMonitoring();
    monitor.stopMonitoring();
  });

  it('reports Docker-down health for expected services', async () => {
    const { HealthMonitor } = await loadHealth();
    const monitor = HealthMonitor.getInstance();
    monitor.stopMonitoring();
    currentMode = 'full-stack';
    execResponses = [{ match: 'docker info', error: new Error('no daemon') }];

    const status = await monitor.checkHealth();
    expect(status.dockerRunning).toBe(false);
    expect(status.overall).toBe('unhealthy');
    expect(status.services[0]).toEqual(expect.objectContaining({
      status: 'error',
      message: 'Docker not running',
    }));

    const detailed = await monitor.getDetailedHealth();
    expect(detailed.recommendations).toContain('Start Docker Desktop to enable Cyber-AutoAgent');
  });

  it('covers health monitor no-service mode, subscriber replay, and uptime formatting branches', async () => {
    const { HealthMonitor } = await loadHealth();
    const monitor = HealthMonitor.getInstance() as any;
    monitor.stopMonitoring();
    currentMode = 'single-container';
    execResponses = [{ match: 'docker info', stdout: 'ok' }];

    const status = await monitor.checkHealth();
    expect(status).toEqual(expect.objectContaining({
      dockerRunning: true,
      overall: 'healthy',
      services: [],
    }));

    const replay = jest.fn();
    const unsubscribe = monitor.subscribe(replay);
    expect(replay).toHaveBeenCalledWith(status);
    unsubscribe();

    expect(monitor.formatUptime(999)).toBe('0s');
    expect(monitor.formatUptime(65_000)).toBe('1m 5s');
    expect(monitor.formatUptime(3_660_000)).toBe('1h 1m');
    expect(monitor.formatUptime(90_000_000)).toBe('1d 1h');

    execResponses = [{ match: 'docker info', error: new Error('string-like failure') }];
    currentMode = 'single-container';
    await expect(monitor.checkHealth()).resolves.toEqual(expect.objectContaining({
      overall: 'unhealthy',
      services: [],
    }));
  });
});
