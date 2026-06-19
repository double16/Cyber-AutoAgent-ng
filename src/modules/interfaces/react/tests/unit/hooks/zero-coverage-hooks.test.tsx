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

    it('useGlobalKeyboard ignores shortcuts when inactive, non-TTY, or callbacks are absent', async () => {
        const {useGlobalKeyboard} = await import('../../../src/hooks/useGlobalKeyboard.js');
        const onEscape = jest.fn();

        let hook = renderHook(() => useGlobalKeyboard({onEscape, isActive: false}));
        expect(inputHandlers.at(-1)!.options).toEqual({isActive: false});
        act(() => {
            inputHandlers.at(-1)!.handler('', {escape: true});
        });
        expect(onEscape).not.toHaveBeenCalled();
        hook.unmount();

        Object.defineProperty(process.stdin, 'isTTY', {value: false, configurable: true});
        hook = renderHook(() => useGlobalKeyboard({onEscape}));
        expect(inputHandlers.at(-1)!.options).toEqual({isActive: false});
        act(() => {
            inputHandlers.at(-1)!.handler('', {escape: true});
        });
        expect(onEscape).not.toHaveBeenCalled();
        hook.unmount();

        Object.defineProperty(process.stdin, 'isTTY', {value: true, configurable: true});
        hook = renderHook(() => useGlobalKeyboard({}));
        expect(() => {
            act(() => {
                inputHandlers.at(-1)!.handler('', {escape: true});
                inputHandlers.at(-1)!.handler('c', {ctrl: true});
                inputHandlers.at(-1)!.handler('l', {ctrl: true});
            });
        }).not.toThrow();
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

    it('useKeyboardHandlers covers inactive terminal and global escape paths', async () => {
        const {useKeyboardHandlers} = await import('../../../src/hooks/useKeyboardHandlers.js');
        const props = {
            activeOperation: null,
            isTerminalInteractive: false,
            onAssessmentPause: jest.fn(),
            onAssessmentCancel: jest.fn(),
            onScreenClear: jest.fn(),
            onEscapeExit: jest.fn(),
            allowGlobalEscape: true,
        };

        let hook = renderHook(() => useKeyboardHandlers(props as any));
        expect(inputHandlers.at(-2)!.options).toEqual({isActive: false});
        expect(inputHandlers.at(-1)!.options).toEqual({isActive: true});
        act(() => {
            inputHandlers.at(-2)!.handler('', {escape: true});
            inputHandlers.at(-1)!.handler('x', {});
            inputHandlers.at(-1)!.handler('', {escape: true});
        });
        expect(props.onEscapeExit).toHaveBeenCalledTimes(1);
        expect(props.onAssessmentCancel).not.toHaveBeenCalled();
        hook.unmount();

        props.activeOperation = {id: 'op-1', status: 'running'} as any;
        props.onEscapeExit = undefined as any;
        hook = renderHook(() => useKeyboardHandlers(props as any));
        act(() => {
            inputHandlers.at(-1)!.handler('', {escape: true});
        });
        expect(props.onAssessmentCancel).toHaveBeenCalledTimes(1);
        hook.unmount();

        props.activeOperation = null;
        props.allowGlobalEscape = true;
        hook = renderHook(() => useKeyboardHandlers(props as any));
        act(() => {
            inputHandlers.at(-1)!.handler('', {escape: true});
        });
        expect(exit).toHaveBeenCalled();
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

    it('useAutoRun skips until required flags and config are ready and uses explicit objective', async () => {
        const {useAutoRun} = await import('../../../src/hooks/useAutoRun.js');
        const registerTimeout = jest.fn((fn: () => void) => fn());
        const operationManager = {
            assessmentFlowManager: {
                processUserInput: jest.fn(),
            },
            startAssessmentExecution: jest.fn(),
        };
        const actions = {dismissInit: jest.fn()};

        let hook = renderHook(() => useAutoRun({
            autoRun: false,
            target: 'example.com',
            module: 'web',
            appState: {isConfigLoaded: true},
            actions,
            applicationConfig: {},
            operationManager,
            registerTimeout,
        }));
        expect(actions.dismissInit).not.toHaveBeenCalled();
        hook.unmount();

        hook = renderHook(() => useAutoRun({
            autoRun: true,
            target: 'example.com',
            module: 'web',
            objective: 'find exposed admin panels',
            iterations: 1,
            provider: 'ollama',
            model: 'llama',
            region: 'us-west-2',
            appState: {isConfigLoaded: true},
            actions,
            applicationConfig: {iterations: 1, modelProvider: 'ollama', modelId: 'llama', awsRegion: 'us-west-2'},
            operationManager,
            registerTimeout,
        }));
        expect(operationManager.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('objective find exposed admin panels');
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

    it('useDeploymentDetection respects loading, forced setup, existing healthy config, and first-run failures', async () => {
        const {useDeploymentDetection} = await import('../../../src/hooks/useDeploymentDetection.js');
        const actions = {setInitializationFlow: jest.fn()};
        const openConfig = jest.fn();

        let hook = renderHook(() => useDeploymentDetection({
            isConfigLoading: true,
            appState: {isInitializationFlowActive: false, hasUserDismissedInit: false, isConfigLoaded: true},
            actions,
            applicationConfig: {},
            activeModal: 'none' as any,
            openConfig,
        }));
        expect(detectDeployments).not.toHaveBeenCalled();
        hook.unmount();

        process.env.CYBER_SHOW_SETUP = 'true';
        detectDeployments.mockResolvedValueOnce({availableDeployments: [], needsSetup: false});
        hook = renderHook(() => useDeploymentDetection({
            isConfigLoading: false,
            appState: {isInitializationFlowActive: false, hasUserDismissedInit: true, isConfigLoaded: true},
            actions,
            applicationConfig: {isConfigured: true},
            activeModal: 'none' as any,
            openConfig,
        }));
        await act(async () => {
            await Promise.resolve();
        });
        expect(actions.setInitializationFlow).toHaveBeenCalledWith(true);
        hook.unmount();
        delete process.env.CYBER_SHOW_SETUP;

        detectDeployments.mockResolvedValueOnce({availableDeployments: [{mode: 'docker', isHealthy: true}], needsSetup: false});
        hook = renderHook(() => useDeploymentDetection({
            isConfigLoading: false,
            appState: {isInitializationFlowActive: false, hasUserDismissedInit: true, isConfigLoaded: true},
            actions,
            applicationConfig: {isConfigured: true, deploymentMode: 'docker', modelId: 'claude'},
            activeModal: 'config' as any,
            openConfig,
        }));
        await act(async () => {
            await Promise.resolve();
        });
        expect(openConfig).not.toHaveBeenCalledWith('Please configure your AI model and provider settings to continue.');
        hook.unmount();

        detectDeployments.mockRejectedValueOnce(new Error('boom'));
        hook = renderHook(() => useDeploymentDetection({
            isConfigLoading: false,
            appState: {isInitializationFlowActive: false, hasUserDismissedInit: false, isConfigLoaded: true},
            actions,
            applicationConfig: {isConfigured: false},
            activeModal: 'none' as any,
            openConfig,
        }));
        await act(async () => {
            await Promise.resolve();
        });
        expect(actions.setInitializationFlow).toHaveBeenCalledWith(true);
        hook.unmount();
    });
});
