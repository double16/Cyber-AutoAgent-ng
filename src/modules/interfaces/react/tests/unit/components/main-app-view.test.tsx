import React from 'react';
import TestRenderer, {ReactTestRenderer, act} from '../test-renderer.js';
import {beforeEach, describe, expect, it, jest} from '@jest/globals';
import {ModalType} from '../../../src/hooks/useModalManager.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const pauseMonitoring = jest.fn();
const resumeMonitoring = jest.fn();
const checkHealth = jest.fn();
const setOperationTerminalTitle = jest.fn();

jest.unstable_mockModule('../../../src/utils/terminalTitle.js', () => ({
    setOperationTerminalTitle,
}));

jest.unstable_mockModule('../../../src/services/HealthMonitor.js', () => ({
    HealthMonitor: {
        getInstance: () => ({pauseMonitoring, resumeMonitoring, checkHealth}),
    },
}));

jest.unstable_mockModule('../../../src/components/Header.js', () => ({
    Header: ({exitNotice}: any) => <header>header:{String(exitNotice)}</header>,
}));

jest.unstable_mockModule('../../../src/components/Footer.js', () => ({
    Footer: ({operationName, connectionStatus, thinkingStatus, operationHealth}: any) => (
        <footer>footer:{operationName}:{connectionStatus}:{thinkingStatus?.active ? `${thinkingStatus.context}:${thinkingStatus.taskTitle || ''}` : 'idle'}:{operationHealth?.band || 'no-health'}</footer>
    ),
}));

jest.unstable_mockModule('../../../src/components/UnifiedInputPrompt.js', () => ({
    UnifiedInputPrompt: ({onInput, disabled, userHandoffActive}: any) => (
        <button onClick={() => onInput('scan example.com')}>input:{String(disabled)}:{String(userHandoffActive)}</button>
    ),
}));

jest.unstable_mockModule('../../../src/components/Terminal.js', () => ({
    Terminal: ({onEvent, onMetricsUpdate, onHealthUpdate, onThinkingUpdate, animationsEnabled}: any) => (
        <>
            <button onClick={() => {
                onEvent({type: 'task_started', title: 'Enumerate target'});
                onThinkingUpdate({active: true, context: 'tool_execution'});
            }}>terminal:start:{String(animationsEnabled)}</button>
            <button onClick={() => {
                onEvent({type: 'output', content: 'stream has begun'});
                onThinkingUpdate({active: true, context: 'tool_execution'});
                onEvent({type: 'task_started', title: 'Late task title'});
            }}>terminal:late-task</button>
            <button onClick={() => {
                onEvent({type: 'task_done', title: 'Late task title'});
            }}>terminal:task-done</button>
            <button onClick={() => {
                onEvent({type: 'task_deferred', title: 'Late task title'});
            }}>terminal:task-deferred</button>
            <button onClick={() => {
                onEvent({type: 'output'});
                onEvent({type: 'operation_complete'});
                onThinkingUpdate({active: false});
                onMetricsUpdate({tokens: 10});
            }}>terminal:complete</button>
            <button onClick={() => {
                onHealthUpdate({status: 'available', score: 0.84, band: 'good'});
            }}>terminal:health</button>
        </>
    ),
}));

jest.unstable_mockModule('../../../src/components/ModalRegistry.js', () => ({
    ModalRegistry: ({onClose, terminalWidth}: any) => <button onClick={onClose}>modal:{terminalWidth}</button>,
}));

const load = async () => {
    const {MainAppView} = await import('../../../src/components/MainAppView.js');
    return {MainAppView};
};

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

const createProps = (overrides: Record<string, any> = {}) => ({
    appState: {
        terminalDisplayWidth: 100,
        activeOperation: null,
        executionService: null,
        userHandoffActive: false,
        isDockerServiceAvailable: true,
        operationMetrics: {totalCost: 0},
        ...overrides.appState,
    },
    actions: {
        updateMetrics: jest.fn(),
        updateHealth: jest.fn(),
        ...overrides.actions,
    },
    currentTheme: {
        error: 'red',
        success: 'green',
        foreground: 'white',
        muted: 'gray',
    },
    operationHistoryEntries: [
        {id: '1', type: 'command', content: '/hidden', timestamp: new Date('2026-06-18T10:00:00Z')},
        {id: '2', type: 'info', content: 'visible history', timestamp: new Date('2026-06-18T10:01:00Z')},
    ],
    assessmentFlowState: {},
    staticKey: 1,
    activeModal: ModalType.NONE,
    modalContext: {},
    isTerminalInteractive: true,
    onInput: jest.fn(),
    onModalClose: jest.fn(),
    addOperationHistoryEntry: jest.fn(),
    onSafetyConfirm: jest.fn(),
    applicationConfig: {modelProvider: 'bedrock', deploymentMode: 'local-cli'},
    ...overrides,
});

describe('MainAppView', () => {
    beforeEach(() => {
        pauseMonitoring.mockClear();
        resumeMonitoring.mockClear();
        checkHealth.mockClear();
        setOperationTerminalTitle.mockClear();
        delete (global as any).__inkInputHandler;
        delete process.env.CYBER_MAX_HISTORY_RENDERED;
    });

    it('renders header, filtered history, input, and footer on the main screen', async () => {
        const {MainAppView} = await load();
        const props = createProps();
        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<MainAppView {...props as any}/>);
            await new Promise(resolve => setTimeout(resolve, 0));
            await Promise.resolve();
        });
        const text = textFromTree(view.toJSON());

        expect(text).toContain('header:false');
        expect(text).toContain('visible history');
        expect(text).not.toContain('/hidden');
        expect(text).toContain('input:false:false');
        expect(text).toContain('footer::connected');

        act(() => {
            view.root.findByType('button').props.onClick();
        });
        expect(props.onInput).toHaveBeenCalledWith('scan example.com');
    });

    it('updates and resets the terminal title from operation health', async () => {
        const {MainAppView} = await load();
        const health = {status: 'available', score: 0.86, band: 'good'};
        const props = createProps({
            appState: {
                activeOperation: {id: 'op-title', status: 'running', target: 'https://target.test'},
                executionService: {name: 'service'},
                operationHealth: health,
            },
        });

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<MainAppView {...props as any}/>);
            await Promise.resolve();
        });
        expect(setOperationTerminalTitle).toHaveBeenCalledWith(
            health,
            'https://target.test',
            expect.anything(),
        );

        await act(async () => {
            view.update(<MainAppView {...createProps({
                appState: {
                    activeOperation: {id: 'op-title', status: 'complete'},
                    executionService: {name: 'service'},
                    operationHealth: health,
                },
            }) as any}/>);
            await Promise.resolve();
        });
        expect(setOperationTerminalTitle).toHaveBeenLastCalledWith(null, null, expect.anything());
    });

    it('renders modals and operation streams while forwarding lifecycle metrics', async () => {
        const {MainAppView} = await load();
        const updateMetrics = jest.fn();
        const updateHealth = jest.fn();
        const onModalClose = jest.fn();
        const props = createProps({
            actions: {updateMetrics, updateHealth},
            activeModal: ModalType.CONFIG,
            onModalClose,
            appState: {
                activeOperation: {id: 'op-1', status: 'running', model: 'claude', description: 'Running test'},
                executionService: {name: 'service'},
                terminalDisplayWidth: 90,
                userHandoffActive: false,
                isDockerServiceAvailable: false,
            },
        });

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<MainAppView {...props as any}/>);
            await Promise.resolve();
        });
        expect(textFromTree(view.toJSON())).toContain('modal:90');
        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('modal'))!.props.onClick();
        });
        expect(onModalClose).toHaveBeenCalled();

        await act(async () => {
            view.update(<MainAppView {...{...props, activeModal: ModalType.NONE} as any}/>);
            await Promise.resolve();
        });
        await act(async () => {
            await new Promise(resolve => setTimeout(resolve, 10));
        });
        expect(pauseMonitoring).toHaveBeenCalled();

        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('terminal:complete'))!.props.onClick();
        });
        expect(updateMetrics).toHaveBeenCalledWith({tokens: 10});

        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('terminal:health'))!.props.onClick();
        });
        expect(updateHealth).toHaveBeenCalledWith({status: 'available', score: 0.84, band: 'good'});
        expect(textFromTree(view.toJSON())).toContain('footer::offline:idle');

        await act(async () => {
            view.update(<MainAppView {...createProps({actions: {updateMetrics}}) as any}/>);
            await Promise.resolve();
        });
        expect(resumeMonitoring).toHaveBeenCalled();
        expect(checkHealth).toHaveBeenCalled();
    });

    it('merges task_started title into active footer thinking status', async () => {
        const {MainAppView} = await load();
        const props = createProps({
            appState: {
                activeOperation: {id: 'op-2', status: 'running', model: 'claude', description: 'Running test'},
                executionService: {name: 'service'},
                isDockerServiceAvailable: true,
            },
        });

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<MainAppView {...props as any}/>);
            await Promise.resolve();
        });
        await act(async () => {
            await new Promise(resolve => setTimeout(resolve, 10));
        });

        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('terminal:start'))!.props.onClick();
        });

        expect(textFromTree(view.toJSON())).toContain('footer:Running test:connected:tool_execution:Enumerate target');
    });

    it('updates footer task title even after stream content has begun', async () => {
        const {MainAppView} = await load();
        const props = createProps({
            appState: {
                activeOperation: {id: 'op-late-task', status: 'running', model: 'claude', description: 'Running test'},
                executionService: {name: 'service'},
                isDockerServiceAvailable: true,
            },
        });

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<MainAppView {...props as any}/>);
            await Promise.resolve();
        });
        await act(async () => {
            await new Promise(resolve => setTimeout(resolve, 10));
        });

        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('terminal:late-task'))!.props.onClick();
        });

        expect(textFromTree(view.toJSON())).toContain('footer::connected:tool_execution:Late task title');

        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('terminal:task-done'))!.props.onClick();
        });

        expect(textFromTree(view.toJSON())).toContain('footer::connected:tool_execution:');
        expect(textFromTree(view.toJSON())).not.toContain('Late task title');

        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('terminal:late-task'))!.props.onClick();
        });
        expect(textFromTree(view.toJSON())).toContain('Late task title');

        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('terminal:task-deferred'))!.props.onClick();
        });
        expect(textFromTree(view.toJSON())).not.toContain('Late task title');
    });

    it('clears deferred stream mount timer on unmount', async () => {
        jest.useFakeTimers();
        const {MainAppView} = await load();
        const setTimeoutSpy = jest.spyOn(global, 'setTimeout');
        const clearTimeoutSpy = jest.spyOn(global, 'clearTimeout');
        const props = createProps({
            appState: {
                activeOperation: {id: 'op-3', status: 'running', model: 'claude', description: 'Running test'},
                executionService: {name: 'service'},
            },
        });

        let view!: ReactTestRenderer;
        try {
            await act(async () => {
                view = TestRenderer.create(<MainAppView {...props as any}/>);
                await Promise.resolve();
            });

            expect(jest.getTimerCount()).toBeGreaterThan(0);
            const deferTimerIndex = setTimeoutSpy.mock.calls.findIndex(call => call[1] === 0);
            expect(deferTimerIndex).toBeGreaterThanOrEqual(0);
            const deferTimer = setTimeoutSpy.mock.results[deferTimerIndex]?.value;

            act(() => {
                view.unmount();
            });

            expect(clearTimeoutSpy).toHaveBeenCalledWith(deferTimer);
        } finally {
            setTimeoutSpy.mockRestore();
            clearTimeoutSpy.mockRestore();
            jest.useRealTimers();
        }
    });
});
