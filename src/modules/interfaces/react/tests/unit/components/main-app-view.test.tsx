import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {beforeEach, describe, expect, it, jest} from '@jest/globals';
import {ModalType} from '../../../src/hooks/useModalManager.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const pauseMonitoring = jest.fn();
const resumeMonitoring = jest.fn();
const checkHealth = jest.fn();

jest.unstable_mockModule('../../../src/services/HealthMonitor.js', () => ({
    HealthMonitor: {
        getInstance: () => ({pauseMonitoring, resumeMonitoring, checkHealth}),
    },
}));

jest.unstable_mockModule('../../../src/components/Header.js', () => ({
    Header: ({exitNotice}: any) => <header>header:{String(exitNotice)}</header>,
}));

jest.unstable_mockModule('../../../src/components/Footer.js', () => ({
    Footer: ({operationName, connectionStatus}: any) => <footer>footer:{operationName}:{connectionStatus}</footer>,
}));

jest.unstable_mockModule('../../../src/components/UnifiedInputPrompt.js', () => ({
    UnifiedInputPrompt: ({onInput, disabled, userHandoffActive}: any) => (
        <button onClick={() => onInput('scan example.com')}>input:{String(disabled)}:{String(userHandoffActive)}</button>
    ),
}));

jest.unstable_mockModule('../../../src/components/Terminal.js', () => ({
    Terminal: ({onEvent, onMetricsUpdate, animationsEnabled}: any) => (
        <button onClick={() => {
            onEvent({type: 'output'});
            onEvent({type: 'operation_complete'});
            onMetricsUpdate({tokens: 10});
        }}>terminal:{String(animationsEnabled)}</button>
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
        delete (global as any).__inkInputHandler;
        delete process.env.CYBER_MAX_HISTORY_RENDERED;
    });

    it('renders header, filtered history, input, and footer on the main screen', async () => {
        const {MainAppView} = await load();
        const props = createProps();
        let view!: TestRenderer.ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<MainAppView {...props as any}/>);
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

    it('renders modals and operation streams while forwarding lifecycle metrics', async () => {
        const {MainAppView} = await load();
        const updateMetrics = jest.fn();
        const onModalClose = jest.fn();
        const props = createProps({
            actions: {updateMetrics},
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

        let view!: TestRenderer.ReactTestRenderer;
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
        expect(pauseMonitoring).toHaveBeenCalled();

        act(() => {
            view.root.findAllByType('button').find(button => textFromTree(button.props.children).includes('terminal'))!.props.onClick();
        });
        expect(updateMetrics).toHaveBeenCalledWith({tokens: 10});
        expect(textFromTree(view.toJSON())).toContain('footer::offline');

        await act(async () => {
            view.update(<MainAppView {...createProps({actions: {updateMetrics}}) as any}/>);
            await Promise.resolve();
        });
        expect(resumeMonitoring).toHaveBeenCalled();
        expect(checkHealth).toHaveBeenCalled();
    });
});
