import React from 'react';
import { EventEmitter } from 'events';
import { jest } from '@jest/globals';
import TestRenderer, { act } from 'react-test-renderer';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const updateConfig = jest.fn();
const saveConfig = jest.fn(async () => undefined);
let configState = { deploymentMode: 'full-stack', isConfigured: false };

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
  useConfig: () => ({
    config: configState,
    updateConfig,
    saveConfig,
  }),
}));

const pythonService = {
  checkPythonVersion: jest.fn(async () => ({ installed: true, version: '3.11.8' })),
  setupPythonEnvironment: jest.fn(async (onMessage?: (message: string) => void) => {
    onMessage?.('Creating virtual environment');
    onMessage?.('Installing dependencies');
  }),
};

jest.unstable_mockModule('../../../src/services/PythonExecutionService.js', () => ({
  PythonExecutionService: jest.fn(() => pythonService),
}));

const containerManager = new EventEmitter() as any;
containerManager.switchToMode = jest.fn(async () => {
  containerManager.emit('progress', 'Pulling image');
  containerManager.emit('progress', 'Starting services');
});

jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({
  ContainerManager: {
    getInstance: () => containerManager,
  },
}));

const healthMonitor = {
  checkHealth: jest.fn(async () => ({
    dockerRunning: true,
    overall: 'healthy',
    services: [
      { name: 'cyber-langfuse', displayName: 'Langfuse', status: 'running' },
      { name: 'cyber-autoagent', displayName: 'Agent', status: 'running' },
      { name: 'postgres', displayName: 'Postgres', status: 'running' },
    ],
  })),
};

jest.unstable_mockModule('../../../src/services/HealthMonitor.js', () => ({
  HealthMonitor: {
    getInstance: () => healthMonitor,
  },
}));

const execMock = jest.fn((_command: string, optionsOrCallback: any, maybeCallback?: any) => {
  const callback = typeof optionsOrCallback === 'function' ? optionsOrCallback : maybeCallback;
  callback(null, '', '');
});

jest.unstable_mockModule('child_process', () => ({
  exec: execMock,
}));

jest.unstable_mockModule('ink-spinner', () => ({
  default: ({ type }: { type?: string }) => <span>spinner:{type}</span>,
}));

const load = async () => import('../../../src/components/InitializationFlow.js');

const textFromTree = (node: any): string => {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textFromTree).join('');
  return textFromTree(node.children || []);
};

const sendInput = (input = '', key: Record<string, boolean> = {}) => {
  act(() => {
    (global as any).__inkInputHandler?.(input, key);
  });
};

const wait = async (ms = 0) => {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, ms));
  });
};

describe('InitializationFlow', () => {
  beforeEach(() => {
    updateConfig.mockClear();
    saveConfig.mockClear();
    pythonService.checkPythonVersion.mockClear();
    pythonService.setupPythonEnvironment.mockClear();
    containerManager.switchToMode.mockClear();
    healthMonitor.checkHealth.mockClear();
    execMock.mockClear();
    configState = { deploymentMode: 'full-stack', isConfigured: false };
    delete (global as any).__inkInputHandler;
  });

  it('renders welcome, navigates deployment choices, and exits with escape', async () => {
    const { InitializationFlow } = await load();
    const onComplete = jest.fn();
    let view!: TestRenderer.ReactTestRenderer;

    act(() => {
      view = TestRenderer.create(<InitializationFlow onComplete={onComplete} />);
    });
    expect(textFromTree(view.toJSON())).toContain('Welcome to Cyber-AutoAgent Setup');

    sendInput('', { return: true });
    expect(textFromTree(view.toJSON())).toContain('Choose Your Deployment Mode');
    expect(textFromTree(view.toJSON())).toContain('Local CLI');

    sendInput('', { downArrow: true });
    sendInput('', { downArrow: true });
    sendInput('', { upArrow: true });
    expect(textFromTree(view.toJSON())).toContain('Single Container');

    sendInput('', { escape: true });
    expect(onComplete).toHaveBeenCalledWith();
  });

  it('sets up local CLI mode and auto-completes after success', async () => {
    const { InitializationFlow } = await load();
    const onComplete = jest.fn();
    let view!: TestRenderer.ReactTestRenderer;

    act(() => {
      view = TestRenderer.create(<InitializationFlow onComplete={onComplete} />);
    });

    sendInput('', { return: true });
    sendInput('', { return: true });
    expect(updateConfig).toHaveBeenCalledWith({ deploymentMode: 'local-cli' });
    expect(saveConfig).toHaveBeenCalled();
    expect(textFromTree(view.toJSON())).toContain('Local CLI');

    await wait(120);
    expect(pythonService.checkPythonVersion).toHaveBeenCalled();
    expect(pythonService.setupPythonEnvironment).toHaveBeenCalled();
    expect(textFromTree(view.toJSON())).toContain('Python 3.11.8 detected');
    expect(textFromTree(view.toJSON())).toContain('Installing dependencies');

    await wait(1600);
    expect(onComplete).toHaveBeenCalledWith('✓ Local CLI setup completed successfully!');
  });

  it('sets up container mode with progress logs and health refresh', async () => {
    const { InitializationFlow } = await load();
    const onComplete = jest.fn();
    let view!: TestRenderer.ReactTestRenderer;

    act(() => {
      view = TestRenderer.create(<InitializationFlow onComplete={onComplete} />);
    });

    sendInput('', { return: true });
    sendInput('', { downArrow: true });
    sendInput('', { return: true });

    await wait(120);
    expect(containerManager.switchToMode).toHaveBeenCalledWith('single-container');
    expect(healthMonitor.checkHealth).toHaveBeenCalled();
    expect(textFromTree(view.toJSON())).toContain('Pulling image');
    expect(textFromTree(view.toJSON())).toContain('Container setup complete!');

    await wait(1600);
    expect(onComplete).toHaveBeenCalledWith('✓ Agent Container setup completed successfully!');
  });

  it('shows friendly setup failures and allows retry/back actions', async () => {
    const { InitializationFlow } = await load();
    const onComplete = jest.fn();
    pythonService.checkPythonVersion.mockResolvedValueOnce({
      installed: false,
      error: 'Python 3.11+ is required',
    } as never);

    let view!: TestRenderer.ReactTestRenderer;
    act(() => {
      view = TestRenderer.create(<InitializationFlow onComplete={onComplete} />);
    });

    sendInput('', { return: true });
    sendInput('', { return: true });
    await wait(120);

    expect(textFromTree(view.toJSON())).toContain('Setup Failed');
    expect(textFromTree(view.toJSON())).toContain('Python 3.11 or higher is required');

    sendInput('b');
    expect(textFromTree(view.toJSON())).toContain('Choose Your Deployment Mode');

    pythonService.checkPythonVersion.mockResolvedValueOnce({
      installed: false,
      error: 'Python 3.11+ is required',
    } as never);
    sendInput('', { return: true });
    await wait(120);
    expect(textFromTree(view.toJSON())).toContain('Setup Failed');

    pythonService.checkPythonVersion.mockResolvedValueOnce({ installed: true, version: '3.11.8' } as never);
    sendInput('r');
    await wait(120);
    expect(pythonService.checkPythonVersion).toHaveBeenCalledTimes(3);
    expect(textFromTree(view.toJSON())).toContain('Python environment setup complete!');
    expect(onComplete).not.toHaveBeenCalled();
  });
});
