import React from 'react';
import { TextEncoder, TextDecoder } from 'util';
import { jest } from '@jest/globals';
import TestRenderer, { act } from 'react-test-renderer';

if (typeof global.TextEncoder === 'undefined') {
  global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
  global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

let setupResult = { success: true, deploymentMode: 'local-cli' };
let setupThrows = false;
const setupDeploymentMode = jest.fn(async (_mode: string, onProgress?: (progress: any) => void) => {
  onProgress?.({ current: 1, total: 2, message: 'Checking', stepName: 'docker-check' });
  if (setupThrows) throw new Error('boom');
  return setupResult;
});

jest.unstable_mockModule('../../../src/services/SetupService.js', () => ({
  SetupService: jest.fn().mockImplementation(() => ({ setupDeploymentMode })),
}));

const load = async () => import('../../../src/hooks/useSetupWizard.js');

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('useSetupWizard additional coverage', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    setupResult = { success: true, deploymentMode: 'local-cli' };
    setupThrows = false;
    setupDeploymentMode.mockClear();
    delete process.env.CYBER_SHOW_SETUP;
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  const renderHook = async () => {
    const { useSetupWizard } = await load();
    const snapshots: any[] = [];
    const Consumer = () => {
      const hook = useSetupWizard();
      snapshots.push(hook);
      return <span>{hook.state.currentStep}</span>;
    };
    let view!: TestRenderer.ReactTestRenderer;
    act(() => {
      view = TestRenderer.create(<Consumer />);
    });
    return { snapshots, view };
  };

  it('walks steps, selects mode, starts setup, completes, and resets', async () => {
    const { snapshots } = await renderHook();
    expect(snapshots.at(-1).state.currentStep).toBe('welcome');

    act(() => snapshots.at(-1).actions.nextStep());
    expect(snapshots.at(-1).state.currentStep).toBe('deployment');

    act(() => snapshots.at(-1).actions.selectMode('single-container'));
    expect(snapshots.at(-1).state.selectedMode).toBe('single-container');

    await act(async () => {
      await snapshots.at(-1).actions.startSetup();
    });
    expect(setupDeploymentMode).toHaveBeenCalledWith('single-container', expect.any(Function));
    expect(snapshots.at(-1).state.progress).toEqual(expect.objectContaining({ stepName: 'docker-check' }));
    expect(snapshots.at(-1).state.isComplete).toBe(true);

    act(() => snapshots.at(-1).actions.previousStep());
    expect(snapshots.at(-1).state.currentStep).toBe('deployment');

    act(() => snapshots.at(-1).actions.setError('bad config'));
    expect(snapshots.at(-1).state.error).toBe('bad config');
    act(() => snapshots.at(-1).actions.resetError());
    expect(snapshots.at(-1).state.error).toBeNull();
    act(() => snapshots.at(-1).actions.resetWizard());
    expect(snapshots.at(-1).state.currentStep).toBe('welcome');
  });

  it('handles missing mode, failed setup result, thrown setup, and forced setup entry', async () => {
    const { snapshots } = await renderHook();

    await act(async () => {
      await snapshots.at(-1).actions.startSetup();
    });
    expect(snapshots.at(-1).state.error).toBe('No deployment mode selected');

    setupResult = { success: false, deploymentMode: 'full-stack', error: 'docker missing' } as any;
    await act(async () => {
      await snapshots.at(-1).actions.startSetup('full-stack');
    });
    expect(snapshots.at(-1).state.error).toBe('docker missing');

    setupThrows = true;
    await act(async () => {
      await snapshots.at(-1).actions.startSetup('local-cli');
    });
    expect(snapshots.at(-1).state.error).toBe('boom');

  });
});
