import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {beforeEach, describe, expect, it, jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const ModalType = {
    NONE: 'none',
    CONFIG: 'config',
    MEMORY_SEARCH: 'memorySearch',
    MODULE_SELECTOR: 'moduleSelector',
    SAFETY_WARNING: 'safetyWarning',
    INITIALIZATION: 'initialization',
    DOCUMENTATION: 'documentation',
} as const;

const actions = {
    setConfigLoaded: jest.fn(),
    resetErrorCount: jest.fn(),
    setActiveOperation: jest.fn(),
    clearCompletedOperation: jest.fn(),
    refreshStatic: jest.fn(),
    dismissInit: jest.fn(),
};

const appState: any = {
    isConfigLoaded: false,
    isInitializationFlowActive: false,
    userHandoffActive: false,
    activeOperation: null,
    executionService: null,
    staticKey: 1,
};

const modalManager = {
    activeModal: ModalType.NONE,
    modalContext: {},
    staticKey: 2,
    openConfig: jest.fn(),
    openMemorySearch: jest.fn(),
    openModuleSelector: jest.fn(),
    openSafetyWarning: jest.fn(),
    openDocumentation: jest.fn(),
    closeModal: jest.fn(),
    refreshStatic: jest.fn(),
    refreshStaticOnly: jest.fn(),
};

const operationManager = {
    operationHistoryEntries: [],
    assessmentFlowState: {},
    assessmentFlowManager: {
        setSupportedModules: jest.fn(),
        processUserInput: jest.fn(),
    },
    operationManager: {id: 'manager'},
    addOperationHistoryEntry: jest.fn(),
    setAssessmentFlowState: jest.fn(),
    startAssessmentExecution: jest.fn(),
    clearOperationHistory: jest.fn(),
    handleAssessmentPause: jest.fn(),
    handleAssessmentCancel: jest.fn(async () => undefined),
};

const updateConfig = jest.fn();
const saveConfig = jest.fn(async () => undefined);
const handleUnifiedInput = jest.fn();
const useKeyboardHandlers = jest.fn();
const useDeploymentDetection = jest.fn();
const useAutoRun = jest.fn();

jest.unstable_mockModule('../../src/hooks/useApplicationState.js', () => ({
    useApplicationState: () => ({state: appState, actions}),
}));

jest.unstable_mockModule('../../src/hooks/useModalManager.js', () => ({
    ModalType,
    useModalManager: () => modalManager,
}));

jest.unstable_mockModule('../../src/hooks/useOperationManager.js', () => ({
    useOperationManager: () => operationManager,
}));

jest.unstable_mockModule('../../src/hooks/useKeyboardHandlers.js', () => ({
    useKeyboardHandlers: (...args: any[]) => useKeyboardHandlers(...args),
}));

jest.unstable_mockModule('../../src/hooks/useCommandHandler.js', () => ({
    useCommandHandler: () => ({handleUnifiedInput}),
}));

jest.unstable_mockModule('../../src/hooks/useDeploymentDetection.js', () => ({
    useDeploymentDetection: (...args: any[]) => useDeploymentDetection(...args),
}));

jest.unstable_mockModule('../../src/hooks/useAutoRun.js', () => ({
    useAutoRun: (...args: any[]) => useAutoRun(...args),
}));

jest.unstable_mockModule('../../src/contexts/ConfigContext.js', () => ({
    ConfigProvider: ({children}: any) => <>{children}</>,
    useConfig: () => ({
        config: {
            isConfigured: true,
            deploymentMode: 'local-cli',
            modelProvider: 'bedrock',
            modelId: 'claude',
        },
        isConfigLoading: false,
        updateConfig,
        saveConfig,
    }),
}));

jest.unstable_mockModule('../../src/contexts/ModuleContext.js', () => ({
    ModuleProvider: ({children}: any) => <>{children}</>,
    useModule: () => ({
        availableModules: {
            web: {},
            api: {},
        },
    }),
}));

jest.unstable_mockModule('../../src/components/ErrorBoundary.js', () => ({
    ErrorBoundary: ({children}: any) => <>{children}</>,
}));

jest.unstable_mockModule('../../src/components/InitializationWrapper.js', () => ({
    InitializationWrapper: ({onInitializationComplete, onConfigOpen, mainAppViewProps}: any) => (
        <div>
            <span>wrapper:{mainAppViewProps.staticKey}</span>
            <button onClick={() => onInitializationComplete('done')}>complete-init</button>
            <button onClick={onConfigOpen}>open-config</button>
            <button onClick={() => mainAppViewProps.onModalClose()}>close-modal</button>
            <button onClick={() => mainAppViewProps.onInput('input')}>input</button>
        </div>
    ),
}));

const setAvailableModules = jest.fn();
jest.unstable_mockModule('../../src/services/InputParser.js', () => ({
    InputParser: jest.fn(() => ({setAvailableModules})),
}));

jest.unstable_mockModule('../../src/themes/theme-manager.js', () => ({
    themeManager: {
        getCurrentTheme: () => ({primary: 'cyan', muted: 'gray'}),
    },
}));

const load = async () => {
    const {App} = await import('../../src/App.js');
    return {App};
};

describe('App', () => {
    beforeEach(() => {
        Object.values(actions).forEach(mock => mock.mockClear());
        Object.values(modalManager).forEach(value => {
            if (typeof value === 'function') value.mockClear();
        });
        Object.values(operationManager).forEach(value => {
            if (typeof value === 'function') value.mockClear();
        });
        operationManager.assessmentFlowManager.setSupportedModules.mockClear();
        operationManager.assessmentFlowManager.processUserInput.mockClear();
        updateConfig.mockClear();
        saveConfig.mockClear();
        handleUnifiedInput.mockClear();
        useKeyboardHandlers.mockClear();
        useDeploymentDetection.mockClear();
        useAutoRun.mockClear();
        setAvailableModules.mockClear();
        appState.isConfigLoaded = false;
        appState.activeOperation = null;
        appState.isInitializationFlowActive = false;
        modalManager.activeModal = ModalType.NONE;
    });

    it('wires providers, hooks, module discovery, initialization completion, and modal controls', async () => {
        const {App} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        await act(async () => {
            view = TestRenderer.create(
                <App
                    module="web"
                    target="example.com"
                    objective="audit"
                    autoRun
                    iterations={2}
                    provider="bedrock"
                    model="claude"
                    region="us-east-1"
                />
            );
            await Promise.resolve();
        });

        expect(JSON.stringify(view.toJSON())).toContain('wrapper');
        expect(JSON.stringify(view.toJSON())).toContain('"3"');
        expect(actions.setConfigLoaded).toHaveBeenCalledWith(true);
        expect(setAvailableModules).toHaveBeenCalledWith(['web', 'api']);
        expect(operationManager.assessmentFlowManager.setSupportedModules).toHaveBeenCalledWith(['web', 'api']);
        expect(useDeploymentDetection).toHaveBeenCalledWith(expect.objectContaining({
            isConfigLoading: false,
            activeModal: ModalType.NONE,
            updateConfig,
            saveConfig,
        }));
        expect(useAutoRun).toHaveBeenCalledWith(expect.objectContaining({
            autoRun: true,
            target: 'example.com',
            module: 'web',
            objective: 'audit',
            iterations: 2,
        }));
        expect(useKeyboardHandlers).toHaveBeenCalledWith(expect.objectContaining({
            activeOperation: null,
            isTerminalInteractive: true,
        }));

        act(() => view.root.findAllByType('button')[0].props.onClick());
        expect(actions.dismissInit).toHaveBeenCalled();
        expect(actions.clearCompletedOperation).toHaveBeenCalled();
        expect(actions.refreshStatic).toHaveBeenCalled();
        expect(modalManager.refreshStatic).toHaveBeenCalled();

        act(() => view.root.findAllByType('button')[1].props.onClick());
        expect(modalManager.openConfig).toHaveBeenCalled();

        act(() => view.root.findAllByType('button')[3].props.onClick());
        expect(handleUnifiedInput).toHaveBeenCalledWith('input');
    });
});
