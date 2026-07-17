import React from 'react';
import { EventEmitter } from 'events';
import TestRenderer, {ReactTestRenderer, act} from '../test-renderer.js';
import { describe, it, expect, jest, beforeEach, afterEach } from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const assessmentFlow = {
  getState: jest.fn(() => ({ stage: 'module', module: undefined, target: undefined, objective: undefined })),
  setSupportedModules: jest.fn(),
  setDefaultModule: jest.fn(),
  setModule: jest.fn(),
  resetCompleteWorkflow: jest.fn(),
  getValidatedAssessmentParameters: jest.fn(() => ({
    module: 'web',
    target: 'example.com',
    objective: 'check auth',
    continueOperation: false,
    reportOnly: false,
  })),
};

jest.unstable_mockModule('../../../src/services/AssessmentFlow.js', () => ({
  AssessmentFlow: jest.fn(() => assessmentFlow),
}));

const operation = {
  id: 'op-local',
  module: 'web',
  target: 'example.com',
  status: 'running',
  description: 'check auth',
  findings: 1,
  memoryOps: 0,
  evidence: 0,
  cost: { tokensUsed: 0, estimatedCost: 0 },
};

const operationManager = {
  startOperation: jest.fn(() => operation),
  pauseOperation: jest.fn(() => true),
  updateOperation: jest.fn((_id: string, updates: Record<string, unknown>) => Object.assign(operation, updates)),
  updateTokenUsage: jest.fn((_id: string, input: number, output: number, cost: number) => {
    operation.cost.tokensUsed += input + output;
    operation.cost.estimatedCost += cost;
  }),
  getOperation: jest.fn(() => operation),
  getOperationDuration: jest.fn(() => '5s'),
  renameOperationId: jest.fn((_oldId: string, newId: string) => ({ ...operation, id: newId })),
};

jest.unstable_mockModule('../../../src/services/OperationManager.js', () => ({
  OperationManager: jest.fn(() => operationManager),
}));

let currentModule = 'web';
let availableModules: Record<string, any> = { web: {}, api: {} };

jest.unstable_mockModule('../../../src/contexts/ModuleContext.js', () => ({
  useModule: () => ({ currentModule, availableModules }),
}));

let config = { deploymentMode: 'local-cli', modelProvider: 'openai', modelId: 'gpt-4o' };

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
  useConfig: () => ({ config }),
}));

const executionService = new EventEmitter() as any;
executionService.execute = jest.fn(async () => ({
  result: Promise.resolve({ ok: true }),
  stop: jest.fn(async () => undefined),
}));
executionService.cleanup = jest.fn();
executionService.stop = jest.fn(async () => undefined);

const selectService = jest.fn(async () => ({
  isPreferred: true,
  mode: 'python-cli',
  service: executionService,
  validation: { warnings: ['low disk'] },
}));

class MockSelectionError extends Error {
  diagnostics: string[];
  constructor(message: string, diagnostics: string[] = []) {
    super(message);
    this.diagnostics = diagnostics;
  }
}

jest.unstable_mockModule('../../../src/services/ExecutionServiceFactory.js', () => ({
  DEFAULT_EXECUTION_CONFIG: { preferredMode: undefined, fallbackModes: ['python-cli'] },
  ExecutionServiceSelectionError: MockSelectionError,
  ExecutionServiceFactory: {
    selectService,
  },
}));

const loadHook = async () => import('../../../src/hooks/useOperationManager.js');

function renderHook<T>(hook: () => T) {
  let current: T;
  const Harness = () => {
    current = hook();
    return null;
  };

  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(<Harness />);
  });

  return {
    get current() {
      return current!;
    },
    unmount() {
      act(() => {
        renderer.unmount();
      });
    },
  };
}

const createActions = () => ({
  setActiveOperation: jest.fn(),
  updateOperation: jest.fn(),
  setExecutionService: jest.fn(),
  setUserHandoff: jest.fn(),
  setHasCompletedOperation: jest.fn(),
  clearCompletedOperation: jest.fn(),
  updateMetrics: jest.fn(),
});

describe('useOperationManager', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    Object.assign(operation, {
      id: 'op-local',
      status: 'running',
      findings: 1,
      memoryOps: 0,
      evidence: 0,
      cost: { tokensUsed: 0, estimatedCost: 0 },
    });
    currentModule = 'web';
    availableModules = { web: {}, api: {} };
    config = { deploymentMode: 'local-cli', modelProvider: 'openai', modelId: 'gpt-4o' };
    delete process.env.CYBER_MAX_OPERATION_HISTORY_ENTRIES;
    delete process.env.CYBER_MAX_OPERATION_HISTORY_CONTENT_CHARS;
    jest.clearAllMocks();
    executionService.removeAllListeners();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('tracks history, starts execution, handles events, and cleans up', async () => {
    const { useOperationManager } = await loadHook();
    const actions = createActions();
    const hook = renderHook(() => useOperationManager({
      appState: {
        activeOperation: null,
        executionService: null,
        userHandoffActive: false,
        operationMetrics: { tokens: 3, cost: 0.01, memoryOps: 0, evidence: 0, progressPercent: 5 },
      } as any,
      actions,
      applicationConfig: { modelId: 'gpt-4o' },
      activeModal: 'none',
    }));

    expect(assessmentFlow.setSupportedModules).toHaveBeenCalledWith(['web', 'api']);
    expect(assessmentFlow.setDefaultModule).toHaveBeenCalledWith('web');

    act(() => {
      hook.current.addOperationHistoryEntry('info', 'manual note');
    });
    expect(hook.current.operationHistoryEntries).toEqual([
      expect.objectContaining({ type: 'info', content: 'manual note' }),
    ]);

    let start!: Promise<void>;
    act(() => {
      start = hook.current.startAssessmentExecution();
    });
    await act(async () => {
      jest.advanceTimersByTime(60);
      await start;
    });

    expect(operationManager.startOperation).toHaveBeenCalledWith(
      'web',
      'example.com',
      'check auth',
      'gpt-4o',
      false,
      false,
    );
    expect(selectService).toHaveBeenCalledWith(
      config,
      expect.objectContaining({ preferredMode: 'python-cli', fallbackModes: [] }),
    );
    expect(actions.setActiveOperation).toHaveBeenCalledWith(operation);
    expect(actions.setExecutionService).toHaveBeenCalledWith(executionService);
    expect(executionService.execute).toHaveBeenCalledWith(
      expect.objectContaining({ target: 'example.com' }),
      config,
    );

    act(() => {
      jest.advanceTimersByTime(301);
    });

    act(() => {
      executionService.emit('event', { type: 'operation_init', operation_id: 'backend-op' });
      executionService.emit('event', { type: 'progress_update', step: 1, progressPercent: 40, content: 'Enumerating' });
      executionService.emit('event', {
        type: 'metrics_update',
        metrics: { inputTokens: 10, outputTokens: 5, cost: 0.02, duration: '6s', memoryOps: 2, evidence: 3, progressPercent: 5 },
      });
      executionService.emit('event', { type: 'error', content: 'CRITICAL finding' });
      executionService.emit('event', { type: 'user_handoff' });
    });

    expect(operationManager.renameOperationId).toHaveBeenCalledWith('op-local', 'backend-op');
    expect(operationManager.updateOperation).toHaveBeenCalledWith('backend-op', expect.objectContaining({
      progressPercentage: 40,
    }));
    expect(operationManager.updateTokenUsage).toHaveBeenCalledWith('backend-op', 10, 5, 0.02, 0, 0);
    expect(operationManager.updateOperation).toHaveBeenCalledWith('backend-op', {memoryOps: 2, evidence: 3});
    expect(actions.updateMetrics).toHaveBeenCalledWith(expect.objectContaining({memoryOps: 2, evidence: 3}));
    expect(actions.setUserHandoff).toHaveBeenCalledWith(true);
    expect(hook.current.operationHistoryEntries).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'error', content: 'CRITICAL finding' }),
    ]));

    act(() => {
      jest.advanceTimersByTime(301);
      executionService.emit('event', {
        type: 'metrics_update',
        metrics: {inputTokens: 0, outputTokens: 0, cost: 0, memoryOps: 0, evidence: 0, progressPercent: 40},
      });
    });
    expect(operationManager.updateOperation).toHaveBeenCalledWith('backend-op', {memoryOps: 0, evidence: 0});
    expect(actions.updateMetrics).toHaveBeenCalledWith(expect.objectContaining({memoryOps: 0, evidence: 0}));

    act(() => {
      executionService.emit('complete', { ok: true });
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(operationManager.updateOperation).toHaveBeenCalledWith('backend-op', expect.objectContaining({
      status: 'completed',
    }));
    expect(actions.setHasCompletedOperation).toHaveBeenCalledWith(true);
    expect(assessmentFlow.resetCompleteWorkflow).toHaveBeenCalled();
    expect(executionService.stop).not.toHaveBeenCalled();
    expect(executionService.cleanup).toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(actions.clearCompletedOperation).toHaveBeenCalled();

    hook.unmount();
  });

  it('handles missing assessment parameters, selection errors, pause, and cancel', async () => {
    jest.useRealTimers();
    const { useOperationManager } = await loadHook();
    const activeExecutionService = new EventEmitter() as any;
    activeExecutionService.stop = jest.fn(async () => undefined);
    activeExecutionService.cleanup = jest.fn();
    const executionHandle = { stop: jest.fn(async () => undefined) };
    const activeOperation = { ...operation, id: 'active-op', executionHandle };
    const actions = createActions();

    assessmentFlow.getValidatedAssessmentParameters.mockReturnValueOnce(null as never);
    const hook = renderHook(() => useOperationManager({
      appState: {
        activeOperation,
        executionService: activeExecutionService,
        userHandoffActive: false,
      } as any,
      actions,
      applicationConfig: { modelId: 'gpt-4o' },
    }));

    await act(async () => {
      await hook.current.startAssessmentExecution();
    });
    expect(hook.current.operationHistoryEntries).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'error', content: 'Assessment parameters not properly configured' }),
    ]));

    await act(async () => {
      await hook.current.handleAssessmentPause();
    });
    expect(executionHandle.stop).toHaveBeenCalled();
    expect(activeExecutionService.stop).not.toHaveBeenCalled();
    expect(operationManager.pauseOperation).toHaveBeenCalledWith('active-op');
    expect(actions.setActiveOperation).toHaveBeenCalledWith(null);

    await act(async () => {
      await hook.current.handleAssessmentCancel();
    });
    expect(executionHandle.stop).toHaveBeenCalledTimes(2);
    expect(actions.setUserHandoff).toHaveBeenCalledWith(false);
    expect(assessmentFlow.resetCompleteWorkflow).toHaveBeenCalled();
    expect(hook.current.operationHistoryEntries).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'error', content: 'ESC Kill Switch activated' }),
    ]));

    hook.unmount();
  });

  it('clears operation state when cancellation stop times out', async () => {
    const { useOperationManager } = await loadHook();
    const activeExecutionService = new EventEmitter() as any;
    activeExecutionService.stop = jest.fn(async () => undefined);
    activeExecutionService.cleanup = jest.fn();
    const executionHandle = {
      stop: jest.fn(() => new Promise<void>(() => undefined)),
    };
    const activeOperation = { ...operation, id: 'stuck-op', executionHandle };
    const actions = createActions();

    const hook = renderHook(() => useOperationManager({
      appState: {
        activeOperation,
        executionService: activeExecutionService,
        userHandoffActive: false,
      } as any,
      actions,
      applicationConfig: { modelId: 'gpt-4o' },
    }));

    let cancelPromise!: Promise<void>;
    act(() => {
      cancelPromise = hook.current.handleAssessmentCancel();
    });

    await act(async () => {
      await jest.advanceTimersByTimeAsync(5050);
      await cancelPromise;
    });

    expect(executionHandle.stop).toHaveBeenCalledTimes(1);
    expect(activeExecutionService.cleanup).toHaveBeenCalledTimes(1);
    expect(operationManager.pauseOperation).toHaveBeenCalledWith('stuck-op');
    expect(actions.setActiveOperation).toHaveBeenCalledWith(null);
    expect(actions.setExecutionService).toHaveBeenCalledWith(null);
    expect(actions.setUserHandoff).toHaveBeenCalledWith(false);
    expect(hook.current.operationHistoryEntries).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'error', content: 'ESC Kill Switch activated' }),
      expect.objectContaining({ type: 'error', content: expect.stringContaining('Timed out stopping execution') }),
      expect.objectContaining({ type: 'info', content: 'Operation terminated. Start a new assessment or review partial results.' }),
    ]));

    hook.unmount();
  });

  it('bounds operation history entries and truncates large history content', async () => {
    process.env.CYBER_MAX_OPERATION_HISTORY_ENTRIES = '21';
    process.env.CYBER_MAX_OPERATION_HISTORY_CONTENT_CHARS = '1001';

    const { useOperationManager } = await loadHook();
    const actions = createActions();
    const hook = renderHook(() => useOperationManager({
      appState: {
        activeOperation: null,
        executionService: null,
        userHandoffActive: false,
      } as any,
      actions,
      applicationConfig: { modelId: 'gpt-4o' },
    }));

    act(() => {
      for (let index = 0; index < 25; index += 1) {
        hook.current.addOperationHistoryEntry('info', `entry-${index}-${'x'.repeat(1200)}`, operation as any);
      }
      jest.advanceTimersByTime(150);
    });

    expect(hook.current.operationHistoryEntries).toHaveLength(21);
    expect(hook.current.operationHistoryEntries[0].content).toContain('entry-4');
    expect(hook.current.operationHistoryEntries.at(-1)?.content).toContain('entry-24');
    expect(hook.current.operationHistoryEntries.at(-1)?.content).toContain('history entry truncated');
    expect(hook.current.operationHistoryEntries.at(-1)).not.toHaveProperty('operation');

    hook.unmount();
  });
});
