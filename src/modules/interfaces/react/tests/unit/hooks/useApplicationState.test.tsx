import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';
import {useApplicationState} from '../../../src/hooks/useApplicationState.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

function renderHook<T>(hook: () => T) {
    let current: T;

    const Harness = () => {
        current = hook();
        return null;
    };

    let renderer: TestRenderer.ReactTestRenderer;
    act(() => {
        renderer = TestRenderer.create(<Harness/>);
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

describe('useApplicationState', () => {
    beforeEach(() => {
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.useRealTimers();
        jest.restoreAllMocks();
    });

    it('initializes default application state', () => {
        const hook = renderHook(() => useApplicationState());

        expect(hook.current.state).toEqual(expect.objectContaining({
            isInitialized: false,
            isConfigLoaded: false,
            sessionErrorCount: 0,
            isFirstRunExperience: true,
            isInitializationFlowActive: false,
            hasUserDismissedInit: false,
            isTerminalVisible: false,
            activeOperation: null,
            recentTargets: [],
            terminalDisplayHeight: 24,
            terminalDisplayWidth: 80,
        }));

        hook.unmount();
    });

    it('updates core UI and initialization state through actions', () => {
        const hook = renderHook(() => useApplicationState());

        act(() => {
            hook.current.actions.initializeApp('session-fixed');
            hook.current.actions.setConfigLoaded(true);
            hook.current.actions.setInitializationFlow(true, true);
            hook.current.actions.setTerminalVisible(true);
            hook.current.actions.setStaticNeedsRefresh(true);
            hook.current.actions.updateTerminalSize(120, 40);
            hook.current.actions.setHasCompletedOperation(true);
        });

        expect(hook.current.state).toEqual(expect.objectContaining({
            isInitialized: true,
            sessionId: 'session-fixed',
            isConfigLoaded: true,
            isInitializationFlowActive: true,
            isUserTriggeredSetup: true,
            hasUserDismissedInit: false,
            isTerminalVisible: true,
            staticNeedsRefresh: true,
            terminalDisplayWidth: 120,
            terminalDisplayHeight: 40,
            hasCompletedOperation: true,
        }));

        act(() => {
            hook.current.actions.dismissInit();
            hook.current.actions.clearCompletedOperation();
        });

        expect(hook.current.state.isInitializationFlowActive).toBe(false);
        expect(hook.current.state.hasUserDismissedInit).toBe(true);
        expect(hook.current.state.isUserTriggeredSetup).toBe(false);
        expect(hook.current.state.hasCompletedOperation).toBe(false);

        hook.unmount();
    });

    it('manages active operations, metrics, and execution service', () => {
        const hook = renderHook(() => useApplicationState());
        const operation = {
            id: 'OP_1',
            status: 'running',
            findings: 0,
            currentStep: 0,
        } as any;
        const executionService = {cleanup: jest.fn()} as any;

        act(() => {
            hook.current.actions.updateOperation({status: 'ignored'});
            hook.current.actions.setActiveOperation(operation);
            hook.current.actions.updateOperation({status: 'completed', findings: 2});
            hook.current.actions.setUserHandoff(true);
            hook.current.actions.updateMetrics({tokens: 10});
            hook.current.actions.updateContextUsage(42);
            hook.current.actions.setExecutionService(executionService);
        });

        expect(hook.current.state.activeOperation).toEqual(expect.objectContaining({
            id: 'OP_1',
            status: 'completed',
            findings: 2,
        }));
        expect(hook.current.state.userHandoffActive).toBe(true);
        expect(hook.current.state.contextUsage).toBe(42);
        expect(hook.current.state.executionService).toBe(executionService);
        expect(hook.current.debouncedMetrics).toBeNull();

        act(() => {
            jest.advanceTimersByTime(150);
        });
        expect(hook.current.state.operationMetrics).toEqual({tokens: 10});

        hook.unmount();
    });

    it('deduplicates recent targets and limits history length', () => {
        const hook = renderHook(() => useApplicationState());

        act(() => {
            ['a', 'b', 'c', 'd', 'e', 'f', 'c'].forEach(target => {
                hook.current.actions.addRecentTarget(target);
            });
        });

        expect(hook.current.state.recentTargets).toEqual(['c', 'f', 'e', 'd', 'b']);
        hook.unmount();
    });

    it('tracks errors, docker availability, and static refresh counters', () => {
        const hook = renderHook(() => useApplicationState());
        const initialStaticKey = hook.current.state.staticKey;

        act(() => {
            hook.current.actions.incrementErrorCount();
            hook.current.actions.incrementErrorCount();
            hook.current.actions.setDockerAvailable(true);
            hook.current.actions.refreshStaticImmediate();
            hook.current.actions.refreshStatic();
        });

        expect(hook.current.state.sessionErrorCount).toBe(2);
        expect(hook.current.state.isDockerServiceAvailable).toBe(true);
        expect(hook.current.state.staticKey).toBe(initialStaticKey + 1);

        act(() => {
            jest.advanceTimersByTime(100);
        });
        expect(hook.current.state.staticKey).toBe(initialStaticKey + 2);

        act(() => {
            hook.current.actions.resetErrorCount();
        });
        expect(hook.current.state.sessionErrorCount).toBe(0);

        hook.unmount();
    });

    it('runs registered cleanup callbacks on unmount', () => {
        const cleanup = jest.fn();
        const hook = renderHook(() => useApplicationState());

        act(() => {
            hook.current.actions.registerCleanup(cleanup);
            hook.current.actions.refreshStatic();
            hook.current.actions.updateMetrics({pending: true});
        });

        hook.unmount();

        expect(cleanup).toHaveBeenCalledTimes(1);
    });
});
