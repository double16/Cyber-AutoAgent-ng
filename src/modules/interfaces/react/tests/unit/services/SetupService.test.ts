import { EventEmitter } from 'events';
import { promisify } from 'util';
import { describe, it, expect, jest, beforeEach, afterEach } from '@jest/globals';

type ExecResult = { stdout?: string; error?: Error };

const execResponses = new Map<string, ExecResult>();
const execMock: any = jest.fn();
execMock[promisify.custom] = jest.fn(async (command: string) => {
  const response = [...execResponses.entries()].find(([match]) => command.includes(match))?.[1];
  if (response?.error) throw response.error;
  return { stdout: response?.stdout ?? '', stderr: '' };
});

const containerManager = new EventEmitter() as any;
containerManager.switchToMode = jest.fn(async (mode: string) => {
  if (mode !== 'local-cli') {
    containerManager.emit('progressMeta', { phase: 'pull', phaseRatio: 0.25 });
    containerManager.emit('progressMeta', { phase: 'start', phaseRatio: 0.75 });
  }
});
containerManager.getDeploymentConfig = jest.fn(() => ({
  services: ['cyber-autoagent', 'langfuse-web', 'postgres'],
}));
containerManager.getRunningCountForServices = jest.fn(async () => ({
  running: 2,
  total: 3,
}));

const pythonService = {
  checkPythonVersion: jest.fn(async () => ({ installed: true, version: '3.11.8' })),
  setupPythonEnvironment: jest.fn(async (onMessage?: (message: string) => void) => {
    onMessage?.('Installing dependencies');
  }),
};

const healthMonitor = {
  checkHealth: jest.fn(async () => ({
    services: [
      { name: 'agent', status: 'running' },
      { name: 'postgres', status: 'stopped' },
    ],
  })),
};

jest.unstable_mockModule('child_process', () => ({
  exec: execMock,
}));

jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({
  ContainerManager: {
    getInstance: () => containerManager,
  },
}));

jest.unstable_mockModule('../../../src/services/PythonExecutionService.js', () => ({
  PythonExecutionService: jest.fn(() => pythonService),
}));

jest.unstable_mockModule('../../../src/services/HealthMonitor.js', () => ({
  HealthMonitor: {
    getInstance: () => healthMonitor,
  },
}));

const loadService = async () => import('../../../src/services/SetupService.js');

describe('SetupService', () => {
  beforeEach(() => {
    execResponses.clear();
    execMock.mockClear();
    execMock[promisify.custom].mockClear();
    containerManager.switchToMode.mockClear();
    containerManager.getDeploymentConfig.mockClear();
    containerManager.getRunningCountForServices.mockClear();
    pythonService.checkPythonVersion.mockClear();
    pythonService.setupPythonEnvironment.mockClear();
    healthMonitor.checkHealth.mockClear();
  });

  it('sets up local CLI mode and reports Python progress', async () => {
    const { SetupService } = await loadService();
    const service = new SetupService();
    const progress = jest.fn();

    const result = await service.setupDeploymentMode('local-cli', progress);

    expect(result).toEqual({ success: true, deploymentMode: 'local-cli' });
    expect(containerManager.switchToMode).toHaveBeenCalledWith('local-cli');
    expect(pythonService.checkPythonVersion).toHaveBeenCalled();
    expect(pythonService.setupPythonEnvironment).toHaveBeenCalled();
    expect(progress).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Installing dependencies',
      stepName: 'dependencies',
    }));
    expect(progress).toHaveBeenLastCalledWith(expect.objectContaining({
      message: 'CLI environment verified and ready',
      stepName: 'validation',
    }));
  });

  it('returns setup errors when Python is missing or the mode is unknown', async () => {
    const { SetupService } = await loadService();
    const service = new SetupService();
    pythonService.checkPythonVersion.mockResolvedValueOnce({
      installed: false,
      error: 'Python 3.11 missing',
    } as never);

    const pythonResult = await service.setupDeploymentMode('local-cli');
    expect(pythonResult).toEqual({
      success: false,
      error: 'Python 3.11 missing',
      deploymentMode: 'local-cli',
    });

    const unknownResult = await service.setupDeploymentMode('bad-mode' as any);
    expect(unknownResult).toEqual({
      success: false,
      error: 'Unknown deployment mode: bad-mode',
      deploymentMode: 'bad-mode',
    });
  });

  it('sets up single-container mode with Docker status and progress metadata', async () => {
    const { SetupService } = await loadService();
    const service = new SetupService();
    const progress = jest.fn();
    execResponses.set('docker info', { stdout: 'ok' });

    const result = await service.setupDeploymentMode('single-container', progress);

    expect(result).toEqual({ success: true, deploymentMode: 'single-container' });
    expect(execMock[promisify.custom]).toHaveBeenCalledWith('docker info');
    expect(containerManager.switchToMode).toHaveBeenCalledWith('single-container');
    expect(progress).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Downloading/Building image…',
      meta: { phaseRatio: 0.25 },
    }));
    expect(progress).toHaveBeenLastCalledWith(expect.objectContaining({
      message: 'Container health check passed',
      stepName: 'validation',
    }));
  });

  it('fails container setup early when Docker is unavailable', async () => {
    const { SetupService } = await loadService();
    const service = new SetupService();
    execResponses.set('docker info', { error: new Error('daemon down') });

    const result = await service.setupDeploymentMode('single-container');

    expect(result.success).toBe(false);
    expect(result.error).toContain('Docker Desktop is not running');
    expect(containerManager.switchToMode).not.toHaveBeenCalled();
  });

  it('checks image availability with exact, registry-prefixed, missing, and failing image lists', async () => {
    const { SetupService } = await loadService();
    const service = new SetupService() as any;
    execResponses.set('docker images', {
      stdout: [
        'langfuse/langfuse:3',
        'registry.example.com/postgres:15-alpine',
      ].join('\n'),
    });

    await expect(service.checkImagesAvailability([
      { repo: 'langfuse/langfuse', tag: '3' },
      { repo: 'postgres', tag: '15-alpine' },
      { repo: 'redis', tag: '7' },
    ])).resolves.toEqual({
      total: 3,
      presentCount: 2,
      ratio: 2 / 3,
      missing: ['redis:7'],
    });

    execResponses.set('docker images', { error: new Error('docker failed') });
    await expect(service.checkImagesAvailability([
      { repo: 'minio/minio' },
      { repo: 'clickhouse/clickhouse-server' },
    ])).resolves.toEqual({
      total: 2,
      presentCount: 0,
      ratio: 0,
      missing: ['minio/minio', 'clickhouse/clickhouse-server'],
    });
  });

  it('returns display metadata for deployment modes', async () => {
    const { SetupService } = await loadService();

    expect(SetupService.getDeploymentModeInfo('local-cli')).toEqual(expect.objectContaining({
      name: 'Local CLI',
      requirements: expect.arrayContaining(['Python 3.11+']),
    }));
    expect(SetupService.getDeploymentModeInfo('single-container')).toEqual(expect.objectContaining({
      name: 'Single Container',
    }));
    expect(SetupService.getDeploymentModeInfo('full-stack')).toEqual(expect.objectContaining({
      name: 'Enterprise Stack',
    }));
  });
});
