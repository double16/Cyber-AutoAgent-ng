import React from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';
import TestRenderer, {ReactTestRenderer, act} from '../test-renderer.js';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

jest.unstable_mockModule('ink-spinner', () => ({
    default: ({type}: { type?: string }) => <span>spinner:{type}</span>,
}));

const updateConfig = jest.fn<() => Promise<void>>(async () => undefined);
const saveConfig = jest.fn<() => Promise<void>>(async () => undefined);

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
    useConfig: () => ({
        config: {deploymentMode: 'local-cli', isConfigured: false},
        updateConfig,
        saveConfig,
    }),
}));

const detectDeployments = jest.fn<() => Promise<any>>(async () => ({
    availableDeployments: [
        {mode: 'local-cli', isHealthy: true},
        {mode: 'single-container', isHealthy: false},
        {mode: 'full-stack', isHealthy: true},
    ],
}));

jest.unstable_mockModule('../../../src/services/DeploymentDetector.js', () => ({
    DeploymentDetector: {
        getInstance: () => ({detectDeployments}),
    },
}));

const execMock = jest.fn((command: string, optionsOrCallback: any, maybeCallback?: any) => {
    const callback = typeof optionsOrCallback === 'function' ? optionsOrCallback : maybeCallback;
    if (command.includes('docker ps --filter')) {
        callback(null, 'cyber-autoagent\n', '');
        return;
    }
    callback(null, '', '');
});

jest.unstable_mockModule('child_process', () => ({
    exec: execMock,
    spawn: jest.fn(),
    execFile: jest.fn((_file: string, args: string[], callback: (error: Error | null, result: any) => void) => {
        const stdout = args.includes('--show-current')
            ? 'feature/python-3.12-min-and-docs\n'
            : 'https://github.com/double16/Cyber-AutoAgent-ng.git\n';
        callback(null, {stdout, stderr: ''});
    }),
}));

const readFile = jest.fn<() => Promise<string>>(async () => {
    throw new Error('missing');
});

jest.unstable_mockModule('fs/promises', () => ({
    readFile,
}));

const load = async () => {
    const [
        {render},
        {DeploymentRecovery},
        {DocumentationViewer},
        {SetupProgressScreen},
        {DeploymentSelectionScreen},
    ] = await Promise.all([
        import('ink-testing-library'),
        import('../../../src/components/DeploymentRecovery.js'),
        import('../../../src/components/DocumentationViewer.js'),
        import('../../../src/components/SetupProgressScreen.js'),
        import('../../../src/components/setup/DeploymentSelectionScreen.js'),
    ]);

    return {render, DeploymentRecovery, DocumentationViewer, SetupProgressScreen, DeploymentSelectionScreen};
};

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

const sendInput = (input = '', key: Record<string, boolean> = {}) => {
    act(() => {
        (global as any).__inkInputHandler?.(input, key);
    });
};

const sendDocumentationInput = (input: string) => {
    act(() => {
        (global as any).__inkStdin?.emit('data', Buffer.from(input));
    });
};

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('setup and documentation screens', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        updateConfig.mockClear();
        saveConfig.mockClear();
        detectDeployments.mockClear();
        execMock.mockClear();
        readFile.mockClear();
        delete (global as any).__inkInputHandler;
    });

    afterEach(() => {
        jest.useRealTimers();
        (global as any).__inkStdin?.removeAllListeners('data');
    });

    it('shows SetupProgressScreen running, failed, and complete states with keyboard actions', async () => {
        const {SetupProgressScreen} = await load();
        const onContinue = jest.fn();
        const onRetry = jest.fn();
        const onBackToSetup = jest.fn();
        const logs = [
            {id: '1', timestamp: '00:00', level: 'info', message: 'Checking Docker availability'},
            {id: '2', timestamp: '00:01', level: 'info', message: 'Starting containers'},
            {id: '3', timestamp: '00:02', level: 'info', message: 'Observability complete'},
        ] as any;

        let view!: ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <SetupProgressScreen
                    deploymentMode="full-stack"
                    setupLogs={logs}
                    isComplete={false}
                    hasFailed={false}
                    onContinue={onContinue}
                    onRetry={onRetry}
                    onBackToSetup={onBackToSetup}
                />
            );
        });
        expect(textFromTree(view.toJSON())).toContain('Enterprise Stack');
        expect(textFromTree(view.toJSON())).toContain('Observability complete');

        act(() => {
            view.update(
                <SetupProgressScreen
                    deploymentMode="local-cli"
                    setupLogs={logs}
                    isComplete={false}
                    hasFailed
                    errorMessage="Python missing"
                    onContinue={onContinue}
                    onRetry={onRetry}
                    onBackToSetup={onBackToSetup}
                />
            );
        });
        expect(textFromTree(view.toJSON())).toContain('Setup Failed');
        expect(textFromTree(view.toJSON())).toContain('Python missing');
        sendInput('r');
        sendInput('b');
        expect(onRetry).toHaveBeenCalledTimes(1);
        expect(onBackToSetup).toHaveBeenCalledTimes(1);

        act(() => {
            view.update(
                <SetupProgressScreen
                    deploymentMode="single-container"
                    setupLogs={[{id: '4', timestamp: '00:03', level: 'success', message: 'ready'}] as any}
                    isComplete
                    hasFailed={false}
                    onContinue={onContinue}
                    onRetry={onRetry}
                    onBackToSetup={onBackToSetup}
                />
            );
        });
        expect(textFromTree(view.toJSON())).toContain('Setup Complete');
        sendInput('', {return: true});
        expect(onContinue).toHaveBeenCalled();
    });

    it('loads DocumentationViewer fallback content and handles navigation', async () => {
        const {DocumentationViewer} = await load();
        const onClose = jest.fn();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<DocumentationViewer onClose={onClose}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('Cyber-AutoAgent Documentation');

        sendDocumentationInput('\u001B[B');
        sendDocumentationInput('\u001B[A');
        await act(async () => {
            sendDocumentationInput('\r');
            await Promise.resolve();
        });
        expect(readFile).toHaveBeenCalled();
        expect(textFromTree(view.toJSON())).toContain('Documentation unavailable');
        expect(textFromTree(view.toJSON())).toContain(
            'https://github.com/double16/Cyber-AutoAgent-ng/blob/feature%2Fpython-3.12-min-and-docs/docs/user-instructions.md'
        );

        sendDocumentationInput('G');
        sendDocumentationInput('g');
        sendDocumentationInput('j');
        sendDocumentationInput('k');
        sendDocumentationInput('\u001B[6~');
        sendDocumentationInput('\u001B[5~');
        sendDocumentationInput('\u001B');
        expect(textFromTree(view.toJSON())).toContain('Select a document to read');
        sendDocumentationInput('\u001B');
        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('scrolls documents with vertical keys and changes documents with horizontal keys', async () => {
        const {DocumentationViewer} = await load();
        const content = Array.from({length: 25}, (_, index) => `Line ${index + 1}`).join('\n');
        readFile.mockResolvedValueOnce(content);

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<DocumentationViewer onClose={jest.fn()} selectedDoc={1}/>);
            await Promise.resolve();
        });
        expect(textFromTree(view.toJSON())).toContain('Row 1/25');

        sendDocumentationInput('\u001B[B');
        expect(textFromTree(view.toJSON())).toContain('Row 2/25');
        sendDocumentationInput('j');
        expect(textFromTree(view.toJSON())).toContain('Row 3/25');
        sendDocumentationInput('\u001B[A');
        sendDocumentationInput('k');
        expect(textFromTree(view.toJSON())).toContain('Row 1/25');

        sendDocumentationInput('G');
        expect(textFromTree(view.toJSON())).toContain('Row 6/25');
        sendDocumentationInput('g');
        expect(textFromTree(view.toJSON())).toContain('Row 1/25');

        await act(async () => {
            sendDocumentationInput('\u001B[C');
            sendDocumentationInput('\u001B[C');
            await Promise.resolve();
        });
        expect(textFromTree(view.toJSON())).toContain('Deployment Guide');

        await act(async () => {
            sendDocumentationInput('\u001B[D');
            sendDocumentationInput('\u001B[D');
            await Promise.resolve();
        });
        expect(textFromTree(view.toJSON())).toContain('User Instructions');
    });

    it('recovers and skips DeploymentRecovery paths', async () => {
        const {DeploymentRecovery} = await load();
        const onComplete = jest.fn();
        const onSkip = jest.fn();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <DeploymentRecovery
                    deployment={{mode: 'local-cli', isHealthy: false, details: {venvExists: true}} as any}
                    onComplete={onComplete}
                    onSkip={onSkip}
                />
            );
        });
        expect(textFromTree(view.toJSON())).toContain('Deployment Recovery Needed');

        await act(async () => {
            sendInput('y');
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(execMock).toHaveBeenCalled();
        expect(updateConfig).toHaveBeenCalledWith({
            deploymentMode: 'local-cli',
            isConfigured: true,
            hasSeenWelcome: true,
        });
        act(() => {
            jest.advanceTimersByTime(1500);
        });
        expect(onComplete).toHaveBeenCalledWith(true);

        execMock.mockImplementationOnce((_command: string, _optionsOrCallback: any, maybeCallback?: any) => {
            const callback = typeof _optionsOrCallback === 'function' ? _optionsOrCallback : maybeCallback;
            callback(new Error('docker failed'), '', 'boom');
        });
        act(() => {
            view.unmount();
        });
        await act(async () => {
            view = TestRenderer.create(
                <DeploymentRecovery
                    deployment={{mode: 'single-container', isHealthy: false, details: {}} as any}
                    onComplete={onComplete}
                    onSkip={onSkip}
                />
            );
        });
        await act(async () => {
            sendInput('y');
            await Promise.resolve();
        });
        expect(textFromTree(view.toJSON())).toContain('Failed to recover Docker container');

        sendInput('s');
        expect(onSkip).toHaveBeenCalledTimes(1);
    });

    it('recovers missing local venv, stopped containers, and full-stack deployments', async () => {
        const {DeploymentRecovery} = await load();
        const onComplete = jest.fn();
        const onSkip = jest.fn();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <DeploymentRecovery
                    deployment={{mode: 'local-cli', isHealthy: false, details: {venvExists: false}} as any}
                    onComplete={onComplete}
                    onSkip={onSkip}
                />
            );
        });

        await act(async () => {
            sendInput('y');
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(execMock).toHaveBeenCalledWith('python3 -m venv .venv', expect.any(Function));
        expect(textFromTree(view.toJSON())).toContain('Recovery complete');

        execMock.mockClear();
        execMock.mockImplementation((command: string, optionsOrCallback: any, maybeCallback?: any) => {
            const callback = typeof optionsOrCallback === 'function' ? optionsOrCallback : maybeCallback;
            if (command.includes('docker ps --filter')) {
                callback(null, '', '');
                return;
            }
            if (command.includes('docker ps -a')) {
                callback(null, 'Exited (0) 10 seconds ago\n', '');
                return;
            }
            callback(null, '', '');
        });

        act(() => {
            view.unmount();
        });
        await act(async () => {
            view = TestRenderer.create(
                <DeploymentRecovery
                    deployment={{mode: 'single-container', isHealthy: false, details: {}} as any}
                    onComplete={onComplete}
                    onSkip={onSkip}
                />
            );
        });
        await act(async () => {
            sendInput('', {return: true});
            await Promise.resolve();
            await Promise.resolve();
            await jest.advanceTimersByTimeAsync(3000);
        });
        expect(execMock.mock.calls.map(call => call[0])).toContain('docker ps --filter name=cyber-autoagent --format "{{.Names}}"');

        const fetchMock = jest.fn(async () => ({ok: true}));
        (globalThis as any).fetch = fetchMock;
        act(() => {
            view.unmount();
        });
        await act(async () => {
            view = TestRenderer.create(
                <DeploymentRecovery
                    deployment={{mode: 'full-stack', isHealthy: false, details: {}} as any}
                    onComplete={onComplete}
                    onSkip={onSkip}
                />
            );
        });
        await act(async () => {
            sendInput('Y');
            await Promise.resolve();
            await jest.advanceTimersByTimeAsync(5000);
        });
        expect(execMock).toHaveBeenCalledWith('docker-compose up -d', {cwd: process.cwd()}, expect.any(Function));
        expect(fetchMock).toHaveBeenCalledWith('http://localhost:3000/api/public/health');
        delete (globalThis as any).fetch;
    });

    it('detects active deployments and selects a deployment mode', async () => {
        const {render, DeploymentSelectionScreen} = await load();
        const onSelect = jest.fn();
        const onBack = jest.fn();

        const output = render(
            <DeploymentSelectionScreen onSelect={onSelect} onBack={onBack} terminalWidth={72}/>
        ).lastFrame();
        expect(output).toContain('Deployment options');
        expect(output).toContain('Detecting');
        expect(output).toContain('Enterprise Stack');

        sendInput('1');
        expect(onSelect).toHaveBeenCalledWith('local-cli');
    });
});
