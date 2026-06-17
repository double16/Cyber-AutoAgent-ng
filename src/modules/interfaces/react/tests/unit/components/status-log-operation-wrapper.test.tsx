import React from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {jest} from '@jest/globals';
import TestRenderer, {act} from 'react-test-renderer';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

let healthSubscriber: ((status: any) => void) | undefined;
const unsubscribe = jest.fn();
const stopMonitoring = jest.fn();
const checkHealth = jest.fn();
const getCurrentMode = jest.fn(async () => 'full-stack');

jest.unstable_mockModule('../../../src/services/HealthMonitor.js', () => ({
    HealthMonitor: {
        getInstance: () => ({
            subscribe: jest.fn((callback: (status: any) => void) => {
                healthSubscriber = callback;
                return unsubscribe;
            }),
            checkHealth,
            stopMonitoring,
        }),
    },
}));

jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({
    ContainerManager: {
        getInstance: () => ({
            getCurrentMode,
        }),
    },
}));

jest.unstable_mockModule('../../../src/components/SetupWizard.js', () => ({
    SetupWizard: ({onComplete, terminalWidth}: any) => (
        <div>
            <span>setup:{terminalWidth}</span>
            <button onClick={() => onComplete('done')}>complete</button>
            <button onClick={() => onComplete('skip setup')}>skip</button>
        </div>
    ),
}));

jest.unstable_mockModule('../../../src/components/MainAppView.js', () => ({
    MainAppView: ({marker}: any) => <div>main:{marker}</div>,
}));

const load = async () => {
    const [
        {render},
        {StatusIndicator},
        {OperationStatusDisplay},
        {LogContainer, CompactLogDisplay},
        {InitializationWrapper},
    ] = await Promise.all([
        import('ink-testing-library'),
        import('../../../src/components/StatusIndicator.js'),
        import('../../../src/components/OperationStatusDisplay.js'),
        import('../../../src/components/LogContainer.js'),
        import('../../../src/components/InitializationWrapper.js'),
    ]);

    return {render, StatusIndicator, OperationStatusDisplay, LogContainer, CompactLogDisplay, InitializationWrapper};
};

const healthStatus = {
    overall: 'degraded',
    dockerRunning: false,
    services: [
        {name: 'api', displayName: 'API', status: 'running', health: 'healthy', uptime: '2m'},
        {name: 'db', displayName: 'Database', status: 'stopped', health: 'unhealthy'},
    ],
    lastCheck: new Date(),
};

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

describe('status, log, operation, and wrapper components', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        healthSubscriber = undefined;
        unsubscribe.mockClear();
        stopMonitoring.mockClear();
        checkHealth.mockClear();
        getCurrentMode.mockResolvedValue('full-stack');
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('renders compact StatusIndicator updates and cleanup', async () => {
        const {StatusIndicator} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        await act(async () => {
            view = TestRenderer.create(<StatusIndicator compact deploymentMode="full-stack"/>);
        });
        expect(textFromTree(view.toJSON())).toBe('');
        await act(async () => {
            healthSubscriber?.(healthStatus);
        });
        const output = textFromTree(view.toJSON());
        expect(output).toContain('full-stack');
        expect(output).toContain('1/2');
        expect(output).toContain('Docker Off');
        expect(checkHealth).toHaveBeenCalled();

        act(() => view.unmount());
        expect(unsubscribe).toHaveBeenCalled();
        expect(stopMonitoring).toHaveBeenCalled();
    });

    it('renders detailed StatusIndicator service rows and fallback modes', async () => {
        const {StatusIndicator} = await load();

        let detailed!: TestRenderer.ReactTestRenderer;
        await act(async () => {
            detailed = TestRenderer.create(<StatusIndicator/>);
        });
        await Promise.resolve();
        await act(async () => {
            healthSubscriber?.(healthStatus);
        });
        const frame = textFromTree(detailed.toJSON());
        expect(frame).toContain('Container Status');
        expect(frame).toContain('DEGRADED');
        expect(frame).toContain('Docker is not running');
        expect(frame).toContain('API');
        expect(frame).toContain('Database');

        let cli!: TestRenderer.ReactTestRenderer;
        await act(async () => {
            cli = TestRenderer.create(<StatusIndicator compact deploymentMode="cli"/>);
        });
        await act(async () => {
            healthSubscriber?.({...healthStatus, overall: 'healthy', dockerRunning: true, services: []});
        });
        expect(textFromTree(cli.toJSON())).toContain('Python');

        let agent!: TestRenderer.ReactTestRenderer;
        await act(async () => {
            agent = TestRenderer.create(<StatusIndicator compact deploymentMode="agent"/>);
        });
        await act(async () => {
            healthSubscriber?.({...healthStatus, overall: 'unhealthy', dockerRunning: true});
        });
        expect(textFromTree(agent.toJSON())).toContain('Docker');
    });

    it('renders operation flow and operation status variants', async () => {
        const {render, OperationStatusDisplay} = await load();
        const startTime = new Date(Date.now() - 5000);

        expect(render(
            <OperationStatusDisplay flowState={{step: 'idle'}} showFlowProgress={false}/>
        ).lastFrame()).toBe('');

        const running = render(
            <OperationStatusDisplay
                terminalWidth={120}
                flowState={{step: 'ready', module: 'web', target: 'example.com', objective: 'audit'}}
                currentOperation={{
                    id: 'OP_1',
                    currentStep: 2,
                    totalSteps: 5,
                    description: 'Testing target',
                    startTime,
                    status: 'running',
                    findings: 1,
                }}
            />
        ).lastFrame();

        expect(running).toContain('Setup');
        expect(running).toContain('Module: web');
        expect(running).toContain('Testing target');
        expect(running).toContain('Step 2/5');
        expect(running).toContain('Findings: 1');
        expect(running).toContain('RUNNING');
        expect(running).toContain('ETA');

        for (const status of ['paused', 'completed', 'error', 'cancelled'] as const) {
            expect(render(
                <OperationStatusDisplay
                    flowState={{step: 'target', module: 'web'}}
                    currentOperation={{
                        id: `OP_${status}`,
                        currentStep: 0,
                        totalSteps: 0,
                        description: status,
                        startTime,
                        status,
                    }}
                />
            ).lastFrame()).toContain(status.toUpperCase());
        }
    });

    it('renders log containers with slicing, details, ansi stripping, and compact mode', async () => {
        const {render, LogContainer, CompactLogDisplay} = await load();
        const logs = [
            {id: '1', timestamp: '00:01', level: 'info', message: '\u001b[31mone\u001b[0m'},
            {id: '2', timestamp: '00:02', level: 'success', message: 'two', details: '\u001b[32mdetail\u001b[0m'},
            {id: '3', timestamp: '00:03', level: 'warning', message: 'three'},
            {id: '4', timestamp: '00:04', level: 'error', message: 'four'},
        ] as any;

        expect(render(<LogContainer logs={[]}/>).lastFrame()).toContain('No logs yet');

        const auto = render(<LogContainer logs={logs} maxHeight={2} title="Audit"/>).lastFrame();
        expect(auto).toContain('Audit');
        expect(auto).toContain('4 entries');
        expect(auto).not.toContain('one');
        expect(auto).toContain('three');
        expect(auto).toContain('four');
        expect(auto).toContain('entries above');

        const top = render(<LogContainer logs={logs} maxHeight={2} autoScroll={false} showTimestamps={false}
                                         bordered={false}/>).lastFrame();
        expect(top).toContain('one');
        expect(top).toContain('two');
        expect(top).toContain('detail');
        expect(top).toContain('entries below');

        const compact = render(<CompactLogDisplay logs={logs} maxItems={2} showIcon={false}/>).lastFrame();
        expect(compact).toContain('three');
        expect(compact).toContain('four');
    });

    it('renders InitializationWrapper loading, setup, and main paths', async () => {
        const {render, InitializationWrapper} = await load();
        const baseState = {
            isConfigLoaded: false,
            isInitializationFlowActive: false,
            hasUserDismissedInit: false,
            terminalDisplayWidth: 100,
            staticKey: 1,
        } as any;

        expect(render(
            <InitializationWrapper
                appState={baseState}
                applicationConfig={{}}
                onInitializationComplete={jest.fn()}
                onConfigOpen={jest.fn()}
                mainAppViewProps={{marker: 'x'}}
            />
        ).lastFrame()).toContain('Loading configuration');

        const onComplete = jest.fn();
        const onConfigOpen = jest.fn();
        const setup = render(
            <InitializationWrapper
                appState={{...baseState, isConfigLoaded: true, isInitializationFlowActive: true}}
                applicationConfig={{}}
                onInitializationComplete={onComplete}
                onConfigOpen={onConfigOpen}
                mainAppViewProps={{marker: 'x'}}
            />
        ).lastFrame();
        expect(setup).toContain('setup:100');

        const main = render(
            <InitializationWrapper
                appState={{...baseState, isConfigLoaded: true, hasUserDismissedInit: true}}
                applicationConfig={{modelId: 'm', modelProvider: 'p'}}
                onInitializationComplete={onComplete}
                onConfigOpen={onConfigOpen}
                mainAppViewProps={{marker: 'ready'}}
            />
        ).lastFrame();
        expect(main).toContain('main:ready');
    });
});
