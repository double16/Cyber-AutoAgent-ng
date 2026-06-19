import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let wizardState: any;
const wizardActions = {
    nextStep: jest.fn(),
    previousStep: jest.fn(),
    selectMode: jest.fn(),
    startSetup: jest.fn(async () => undefined),
    setError: jest.fn(),
    resetError: jest.fn(),
};

const updateConfig = jest.fn();
const saveConfig = jest.fn(async () => undefined);
const detectDeployments = jest.fn<() => Promise<any>>();
const clearCache = jest.fn();
const stdout = {write: jest.fn()};

jest.unstable_mockModule('ink', () => ({
    Box: ({children}: any) => <div>{children}</div>,
    Text: ({children}: any) => <span>{children}</span>,
    useStdout: () => ({stdout}),
}));

jest.unstable_mockModule('../../../src/hooks/useSetupWizard.js', () => ({
    useSetupWizard: () => ({
        state: wizardState,
        actions: wizardActions,
    }),
}));

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
    useConfig: () => ({
        config: {deploymentMode: 'local-cli'},
        updateConfig,
        saveConfig,
    }),
}));

jest.unstable_mockModule('../../../src/services/DeploymentDetector.js', () => ({
    DeploymentDetector: {
        getInstance: () => ({detectDeployments, clearCache}),
    },
}));

jest.unstable_mockModule('../../../src/components/setup/WelcomeScreen.js', () => ({
    WelcomeScreen: ({onContinue, onSkip}: any) => (
        <div>
            <button onClick={onContinue}>welcome-continue</button>
            <button onClick={onSkip}>welcome-skip</button>
        </div>
    ),
}));

jest.unstable_mockModule('../../../src/components/setup/DeploymentSelectionScreen.js', () => ({
    DeploymentSelectionScreen: ({onSelect, onBack}: any) => (
        <div>
            <button onClick={() => onSelect('local-cli')}>select-local</button>
            <button onClick={onBack}>deployment-back</button>
        </div>
    ),
}));

jest.unstable_mockModule('../../../src/components/setup/ProgressScreen.js', () => ({
    ProgressScreen: ({deploymentMode, onComplete, onRetry, onBack}: any) => (
        <div>
            <span>progress:{deploymentMode}</span>
            <button onClick={onComplete}>progress-complete</button>
            <button onClick={onRetry}>progress-retry</button>
            <button onClick={onBack}>progress-back</button>
        </div>
    ),
}));

const load = async () => {
    const {SetupWizard} = await import('../../../src/components/SetupWizard.js');
    return {SetupWizard};
};

describe('SetupWizard', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        wizardState = {
            currentStep: 'welcome',
            selectedMode: null,
            progress: null,
            isComplete: false,
            isLoading: false,
            error: null,
        };
        Object.values(wizardActions).forEach(mock => mock.mockClear());
        updateConfig.mockClear();
        saveConfig.mockClear();
        stdout.write.mockClear();
        detectDeployments.mockReset();
        clearCache.mockClear();
        delete process.env.CYBER_SHOW_SETUP;
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('renders welcome actions and completes skipped setup asynchronously', async () => {
        const {SetupWizard} = await load();
        const onComplete = jest.fn();
        process.env.CYBER_SHOW_SETUP = 'true';
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<SetupWizard onComplete={onComplete} terminalWidth={80}/>);
        });

        act(() => view.root.findAllByType('button')[0].props.onClick());
        expect(wizardActions.nextStep).toHaveBeenCalled();

        act(() => view.root.findAllByType('button')[1].props.onClick());
        expect(process.env.CYBER_SHOW_SETUP).toBeUndefined();
        act(() => {
            jest.runOnlyPendingTimers();
        });
        expect(onComplete).toHaveBeenCalledWith('Setup skipped');
    });

    it('fast-switches to an already healthy deployment from the selection screen', async () => {
        const {SetupWizard} = await load();
        wizardState = {...wizardState, currentStep: 'deployment'};
        detectDeployments.mockResolvedValue({
            availableDeployments: [{mode: 'local-cli', isHealthy: true}],
        });
        const onComplete = jest.fn();
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<SetupWizard onComplete={onComplete}/>);
        });

        await act(async () => {
            await view.root.findAllByType('button')[0].props.onClick();
            await Promise.resolve();
            await Promise.resolve();
            jest.advanceTimersByTime(351);
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(wizardActions.selectMode).toHaveBeenCalledWith('local-cli');
        expect(wizardActions.nextStep).toHaveBeenCalled();
        expect(updateConfig).toHaveBeenCalledWith({deploymentMode: 'local-cli', hasSeenWelcome: true, isConfigured: true});
        expect(saveConfig).toHaveBeenCalled();
        expect(clearCache).toHaveBeenCalled();
        act(() => {
            jest.runOnlyPendingTimers();
        });
        expect(onComplete).toHaveBeenCalledWith('Switched to Local CLI deployment');
        expect(wizardActions.startSetup).not.toHaveBeenCalled();
    });

    it('handles progress retry, back, and manual complete', async () => {
        const {SetupWizard} = await load();
        wizardState = {...wizardState, currentStep: 'progress', selectedMode: 'single-container'};
        const onComplete = jest.fn();
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<SetupWizard onComplete={onComplete}/>);
        });

        act(() => view.root.findAllByType('button')[1].props.onClick());
        expect(wizardActions.resetError).toHaveBeenCalled();
        expect(wizardActions.startSetup).toHaveBeenCalled();

        act(() => view.root.findAllByType('button')[2].props.onClick());
        expect(wizardActions.previousStep).toHaveBeenCalled();

        await act(async () => {
            await view.root.findAllByType('button')[0].props.onClick();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(updateConfig).toHaveBeenCalledWith({deploymentMode: 'single-container', hasSeenWelcome: true, isConfigured: true});
        expect(onComplete).toHaveBeenCalledWith('Agent Container setup completed successfully');
    });
});
