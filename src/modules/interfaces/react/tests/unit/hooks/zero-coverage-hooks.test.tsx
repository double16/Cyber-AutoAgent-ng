import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const inputHandlers: Array<{ handler: (input: string, key: any) => void; options?: any }> = [];
const exit = jest.fn();

jest.unstable_mockModule('ink', () => ({
    useInput: (handler: (input: string, key: any) => void, options?: any) => {
        inputHandlers.push({handler, options});
    },
    useApp: () => ({exit}),
    useStdout: () => ({stdout: {write: jest.fn()}}),
}));

const detectDeployments = jest.fn<() => Promise<any>>();
const detector = {
    detectDeployments,
};

jest.unstable_mockModule('../../../src/services/DeploymentDetector.js', () => ({
    DeploymentDetector: {
        getInstance: () => detector,
    },
}));

jest.unstable_mockModule('../../../src/services/LoggingService.js', () => ({
    loggingService: {
        info: jest.fn(),
    },
}));

const renderHook = (hook: () => void) => {
    let renderer!: TestRenderer.ReactTestRenderer;
    const Harness = () => {
        hook();
        return null;
    };

    act(() => {
        renderer = TestRenderer.create(<Harness/>);
    });

    return {
        update() {
            act(() => {
                renderer.update(<Harness/>);
            });
        },
        unmount() {
            act(() => {
                renderer.unmount();
            });
        },
    };
};

describe('zero-coverage hooks', () => {
    beforeEach(() => {
        inputHandlers.length = 0;
        exit.mockClear();
        detectDeployments.mockReset();
        Object.defineProperty(process.stdin, 'isTTY', {value: true, configurable: true});
        delete process.env.CYBER_SHOW_SETUP;
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('useGlobalKeyboard routes only active global shortcuts in a TTY', async () => {
        const {useGlobalKeyboard} = await import('../../../src/hooks/useGlobalKeyboard.js');
        const onEscape = jest.fn();
        const onCtrlC = jest.fn();
        const onCtrlL = jest.fn();

        const hook = renderHook(() => useGlobalKeyboard({onEscape, onCtrlC, onCtrlL}));
        const {handler, options} = inputHandlers.at(-1)!;

        expect(options).toEqual({isActive: true});
        act(() => {
            handler('', {escape: true});
            handler('c', {ctrl: true});
            handler('l', {ctrl: true});
            handler('x', {});
        });

        expect(onEscape).toHaveBeenCalledTimes(1);
        expect(onCtrlC).toHaveBeenCalledTimes(1);
        expect(onCtrlL).toHaveBeenCalledTimes(1);
        hook.unmount();
    });

    it('useKeyboardHandlers handles cancel, pause, clear, and fallback exit keys', async () => {
        const {useKeyboardHandlers} = await import('../../../src/hooks/useKeyboardHandlers.js');
        const props = {
            activeOperation: {id: 'op-1', status: 'running'},
            isTerminalInteractive: true,
            onAssessmentPause: jest.fn(),
            onAssessmentCancel: jest.fn(),
            onScreenClear: jest.fn(),
            onEscapeExit: jest.fn(),
            allowGlobalEscape: false,
        };
        const hook = renderHook(() => useKeyboardHandlers(props as any));

        act(() => {
            inputHandlers[0].handler('', {escape: true});
            inputHandlers[0].handler('c', {ctrl: true});
            inputHandlers[0].handler('l', {ctrl: true});
        });

        expect(props.onAssessmentCancel).toHaveBeenCalledTimes(1);
        expect(props.onAssessmentPause).toHaveBeenCalledTimes(1);
        expect(props.onScreenClear).toHaveBeenCalledTimes(1);

        props.activeOperation = null as any;
        props.onEscapeExit = undefined as any;
        hook.update();
        act(() => {
            inputHandlers.at(-2)!.handler('c', {ctrl: true});
        });
        expect(exit).toHaveBeenCalledTimes(1);
        hook.unmount();
    });

    it('useAutoRun primes assessment flow and schedules execution from CLI flags', async () => {
        const {useAutoRun} = await import('../../../src/hooks/useAutoRun.js');
        const registerTimeout = jest.fn((fn: () => void) => fn());
        const operationManager = {
            assessmentFlowManager: {
                processUserInput: jest.fn(),
            },
            startAssessmentExecution: jest.fn(),
        };
        const actions = {dismissInit: jest.fn()};

        const hook = renderHook(() => useAutoRun({
            autoRun: true,
            target: 'example.com',
            module: 'web',
            objective: undefined,
            iterations: 5,
            provider: 'bedrock',
            model: 'claude',
            region: 'us-east-1',
            appState: {isConfigLoaded: true},
            actions,
            applicationConfig: {iterations: 1, modelProvider: 'ollama', modelId: 'llama', awsRegion: 'us-west-2'},
            operationManager,
            registerTimeout,
        }));

        expect(actions.dismissInit).toHaveBeenCalled();
        expect(operationManager.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('module web');
        expect(operationManager.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('target example.com');
        expect(operationManager.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('');
        expect(registerTimeout).toHaveBeenCalledWith(expect.any(Function), 100);
        expect(operationManager.startAssessmentExecution).toHaveBeenCalled();
        hook.unmount();
    });

    it('useDeploymentDetection shows setup, auto-selects healthy deployments, and prompts missing model config', async () => {
        jest.useFakeTimers();
        const {useDeploymentDetection} = await import('../../../src/hooks/useDeploymentDetection.js');
        const actions = {setInitializationFlow: jest.fn()};
        const updateConfig = jest.fn();
        const saveConfig = jest.fn(async () => undefined);
        const openConfig = jest.fn();

        detectDeployments.mockResolvedValueOnce({availableDeployments: [], needsSetup: true});
        let hook = renderHook(() => useDeploymentDetection({
            isConfigLoading: false,
            appState: {isInitializationFlowActive: false, hasUserDismissedInit: false, isConfigLoaded: true},
            actions,
            applicationConfig: {isConfigured: false},
            activeModal: 'none' as any,
            openConfig,
            updateConfig,
            saveConfig,
        }));
        await act(async () => {
            await Promise.resolve();
        });
        expect(actions.setInitializationFlow).toHaveBeenCalledWith(true);
        hook.unmount();

        detectDeployments.mockResolvedValueOnce({availableDeployments: [{mode: 'local-cli', isHealthy: true}], needsSetup: false});
        hook = renderHook(() => useDeploymentDetection({
            isConfigLoading: false,
            appState: {isInitializationFlowActive: false, hasUserDismissedInit: true, isConfigLoaded: true},
            actions,
            applicationConfig: {isConfigured: false},
            activeModal: 'none' as any,
            openConfig,
            updateConfig,
            saveConfig,
        }));
        await act(async () => {
            await Promise.resolve();
        });
        expect(updateConfig).toHaveBeenCalledWith({deploymentMode: 'local-cli', isConfigured: true, hasSeenWelcome: true});
        expect(saveConfig).toHaveBeenCalled();
        hook.unmount();

        detectDeployments.mockResolvedValueOnce({availableDeployments: [], needsSetup: false});
        hook = renderHook(() => useDeploymentDetection({
            isConfigLoading: false,
            appState: {isInitializationFlowActive: false, hasUserDismissedInit: true, isConfigLoaded: true},
            actions,
            applicationConfig: {isConfigured: true, deploymentMode: 'local-cli', modelId: ''},
            activeModal: 'none' as any,
            openConfig,
            updateConfig,
            saveConfig,
        }));
        await act(async () => {
            await Promise.resolve();
        });
        act(() => {
            jest.advanceTimersByTime(1000);
        });
        expect(openConfig).toHaveBeenCalledWith('Please configure your AI model and provider settings to continue.');
        hook.unmount();
    });
});
