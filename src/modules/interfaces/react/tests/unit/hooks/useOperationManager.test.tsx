import React from 'react';
import { EventEmitter } from 'events';
import TestRenderer, { act } from 'react-test-renderer';
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
  cost: { tokensUsed: 0, estimatedCost: 0 },
};

const operationManager = {
  startOperation: jest.fn(() => operation),
  pauseOperation: jest.fn(() => true),
  updateOperation: jest.fn(),
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

  let renderer!: TestRenderer.ReactTestRenderer;
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
      cost: { tokensUsed: 0, estimatedCost: 0 },
    });
    currentModule = 'web';
    availableModules = { web: {}, api: {} };
    config = { deploymentMode: 'local-cli', modelProvider: 'openai', modelId: 'gpt-4o' };
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
        operationMetrics: { tokens: 3, cost: 0.01, memoryOps: 0, evidence: 0 },
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
      executionService.emit('event', { type: 'operation_init', operation_id: 'backend-op' });
      executionService.emit('event', { step: 2, total_steps: 5, content: 'Enumerating' });
      executionService.emit('event', {
        type: 'metrics_update',
        metrics: { inputTokens: 10, outputTokens: 5, cost: 0.02, duration: '6s', memoryOps: 2, evidence: 3 },
      });
      executionService.emit('event', { type: 'error', content: 'CRITICAL finding' });
      executionService.emit('event', { type: 'user_handoff' });
    });

    expect(operationManager.renameOperationId).toHaveBeenCalledWith('op-local', 'backend-op');
    expect(operationManager.updateOperation).toHaveBeenCalledWith('backend-op', expect.objectContaining({
      currentStep: 2,
      totalSteps: 5,
    }));
    expect(operationManager.updateTokenUsage).toHaveBeenCalledWith('backend-op', 10, 5, 0.02, 0, 0);
    expect(actions.setUserHandoff).toHaveBeenCalledWith(true);
    expect(hook.current.operationHistoryEntries).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'error', content: 'CRITICAL finding' }),
    ]));

    act(() => {
      executionService.emit('complete', { ok: true });
    });
    expect(operationManager.updateOperation).toHaveBeenCalledWith('backend-op', expect.objectContaining({
      status: 'completed',
    }));
    expect(actions.setHasCompletedOperation).toHaveBeenCalledWith(true);
    expect(assessmentFlow.resetCompleteWorkflow).toHaveBeenCalled();
    expect(executionService.cleanup).toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(actions.clearCompletedOperation).toHaveBeenCalled();

    hook.unmount();
  });

  it('handles missing assessment parameters, selection errors, pause, and cancel', async () => {
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
    expect(activeExecutionService.stop).toHaveBeenCalled();
    expect(operationManager.pauseOperation).toHaveBeenCalledWith('active-op');
    expect(actions.setActiveOperation).toHaveBeenCalledWith(null);

    await act(async () => {
      const pending = hook.current.handleAssessmentCancel();
      await Promise.resolve();
      jest.advanceTimersByTime(60);
      await pending;
    });
    expect(executionHandle.stop).toHaveBeenCalled();
    expect(actions.setUserHandoff).toHaveBeenCalledWith(false);
    expect(assessmentFlow.resetCompleteWorkflow).toHaveBeenCalled();
    expect(hook.current.operationHistoryEntries).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'error', content: 'ESC Kill Switch activated' }),
    ]));

    hook.unmount();
  });
});
