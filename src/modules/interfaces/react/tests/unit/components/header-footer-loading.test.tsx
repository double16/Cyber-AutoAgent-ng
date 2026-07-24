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

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
    useConfig: () => ({
        config: {
            deploymentMode: 'local-cli',
            modelProvider: 'bedrock',
        },
    }),
}));

jest.unstable_mockModule('ink-spinner', () => ({
    default: ({type}: { type?: string }) => <span>spinner:{type}</span>,
}));

const load = async () => {
    const [{render}, {Header}, {Footer}, thinking] = await Promise.all([
        import('ink-testing-library'),
        import('../../../src/components/Header.js'),
        import('../../../src/components/Footer.js'),
        import('../../../src/components/ThinkingIndicator.js'),
    ]);

    return {render, Header, Footer, ...thinking};
};

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

describe('header and footer components', () => {
    beforeEach(() => {
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('renders compact, ultra-compact, and ASCII headers', async () => {
        const {render, Header} = await load();

        const compactFrame = render(<Header terminalWidth={50} version="1.2.3" nightly/>).lastFrame();
        expect(compactFrame).toContain('v1.2.3');
        expect(compactFrame).toContain('NIGHTLY');
        expect(render(<Header terminalWidth={20} version="1.2.3"/>).lastFrame()).toContain('v1.2.3');

        const asciiFrame = render(<Header terminalWidth={100} version="1.2.3" nightly exitNotice/>).lastFrame();
        expect(asciiFrame).toContain('Full Spectrum Cyber Operations v1.2.3');
        expect(asciiFrame).toContain('NIGHTLY');
        expect(asciiFrame).toContain('Exiting Cyber-AutoAgent');
    });

    it('renders footer status, metrics, debug state, and truncation', async () => {
        const {render, Footer} = await load();
        const originalColumns = process.stdout.columns;

        try {
            Object.defineProperty(process.stdout, 'columns', {value: 180, configurable: true});

            const frame = render(
                <Footer
                    model="claude"
                    debugMode
                    deploymentMode="local-cli"
                    isOperationRunning
                    isInputPaused={false}
                    connectionStatus="connected"
                    errorCount={2}
                    operationMetrics={{
                        tokens: 12345,
                        cost: 0.004,
                        duration: '5m 10s',
                        progressPercent: 25,
                        memoryOps: 3,
                    }}
                />
            ).lastFrame();

            expect(frame).toContain('local-cli');
            expect(frame).toContain('12,345 tokens');
            expect(frame).toContain('&lt;$0.01');
            expect(frame).toContain('5m 10s');
            expect(frame).toContain('ETA 20m 40s');
            expect(frame).toContain('3 mem');
            expect(frame).toContain('2 errors');
            expect(frame).toContain('style="color:#A6E3A1"');

            Object.defineProperty(process.stdout, 'columns', {value: 24, configurable: true});
            expect(render(
                <Footer
                    deploymentMode="full-stack"
                    isOperationRunning={false}
                    isInputPaused={false}
                    connectionStatus="error"
                    operationMetrics={{cost: 1.25}}
                />
            ).lastFrame().length).toBeLessThanOrEqual(200);
        } finally {
            Object.defineProperty(process.stdout, 'columns', {value: originalColumns, configurable: true});
        }
    });

    it('renders startup thinking as a footer line above unchanged metrics', async () => {
        const {render, Footer} = await load();
        const frame = render(
            <Footer
                deploymentMode="local-cli"
                isOperationRunning
                isInputPaused={false}
                connectionStatus="connected"
                thinkingStatus={{active: true, context: 'startup', startTime: Date.now()}}
                operationMetrics={{tokens: 42, cost: 0}}
            />
        ).lastFrame();

        expect(frame).toContain('Initializing');
        expect(frame).toContain('42 tokens');
        expect(frame).toContain('[ESC] Kill Switch');
    });

    it('renders only valid health score and band prominently at narrow widths', async () => {
        const {render, Footer} = await load();
        const originalColumns = process.stdout.columns;

        try {
            Object.defineProperty(process.stdout, 'columns', {value: 40, configurable: true});
            const frame = render(
                <Footer
                    deploymentMode="local-cli"
                    isOperationRunning
                    isInputPaused={false}
                    operationHealth={{
                        status: 'available',
                        score: 0.84,
                        band: 'good',
                        task_counts: {failed: 2},
                    }}
                />
            ).lastFrame();

            expect(frame).toContain('🩺🟢 84% GOOD');
            expect(frame).toContain('style="color:cyan;font-weight:bold"');
            expect(frame).not.toContain('task_counts');

            const invalidFrame = render(
                <Footer
                    isOperationRunning={false}
                    isInputPaused={false}
                    operationHealth={{status: 'unavailable', score: 0.84, band: 'good'}}
                />
            ).lastFrame();
            expect(invalidFrame).not.toContain('🩺🟢 84% GOOD');
        } finally {
            Object.defineProperty(process.stdout, 'columns', {value: originalColumns, configurable: true});
        }
    });

    it('renders normal thinking details, task title, disabled fallback, and recording glyph', async () => {
        const {render, Footer} = await load();

        const disabledFrame = render(
            <Footer
                isOperationRunning
                isInputPaused={false}
                animationsEnabled={false}
                thinkingStatus={{
                    active: true,
                    context: 'tool_execution',
                    message: 'Running tool',
                    taskTitle: 'Enumerate target',
                    startTime: Date.now(),
                }}
            />
        ).lastFrame();

        expect(disabledFrame).toContain('[BUSY]');
        expect(disabledFrame).toContain('Enumerate target - Running tool');

        process.env.CYBER_RECORDING_MODE = 'true';
        try {
            const recordingFrame = render(
                <Footer
                    isOperationRunning
                    isInputPaused={false}
                    thinkingStatus={{active: true, context: 'reasoning', message: 'Reasoning'}}
                />
            ).lastFrame();
            expect(recordingFrame).toContain('⌛');
        } finally {
            delete process.env.CYBER_RECORDING_MODE;
        }
    });

    it('updates elapsed time without starting phrase timer for explicit ThinkingIndicator messages', async () => {
        const {ThinkingIndicator} = await load();
        jest.setSystemTime(new Date('2026-07-06T12:00:00Z'));
        const startTime = Date.now() - 65_000;
        const setIntervalSpy = jest.spyOn(global, 'setInterval');

        let view!: ReactTestRenderer;
        try {
            await act(async () => {
                view = TestRenderer.create(
                    <ThinkingIndicator
                        context="tool_execution"
                        message="Working"
                        startTime={startTime}
                        maxWidth={120}
                    />
                );
            });

            expect(textFromTree(view.toJSON())).toContain('Working [1m 5s]');
            expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 1000);
            expect(setIntervalSpy).not.toHaveBeenCalledWith(expect.any(Function), 18000);

            await act(async () => {
                jest.advanceTimersByTime(18_000);
            });

            expect(textFromTree(view.toJSON())).toContain('Working');

            act(() => {
                view.unmount();
            });
        } finally {
            setIntervalSpy.mockRestore();
        }
    });

    it('covers ThinkingIndicator recording, disabled, tight truncation, and inline animation paths', async () => {
        const {ThinkingIndicator, InlineThinking} = await load();
        jest.setSystemTime(new Date('2026-07-06T12:00:00Z'));
        process.env.CYBER_RECORDING_MODE = 'true';

        let recording!: ReactTestRenderer;
        await act(async () => {
            recording = TestRenderer.create(
                <ThinkingIndicator
                    context="startup"
                    startTime={Date.now() - 10_000}
                    maxWidth={1}
                />
            );
        });
        expect(textFromTree(recording.toJSON())).toContain('⌛');
        act(() => recording.unmount());
        delete process.env.CYBER_RECORDING_MODE;

        let disabled!: ReactTestRenderer;
        await act(async () => {
            disabled = TestRenderer.create(
                <ThinkingIndicator
                    context="waiting"
                    enabled={false}
                    taskTitle="Task"
                    message="Waiting"
                    maxWidth={12}
                />
            );
        });
        expect(textFromTree(disabled.toJSON())).toContain('[BUSY]');
        act(() => disabled.unmount());

        let tight!: ReactTestRenderer;
        await act(async () => {
            tight = TestRenderer.create(
                <ThinkingIndicator
                    context="waiting"
                    maxWidth={3}
                />
            );
        });
        expect(textFromTree(tight.toJSON()).length).toBeGreaterThan(0);
        act(() => tight.unmount());

        let inline!: ReactTestRenderer;
        await act(async () => {
            inline = TestRenderer.create(<InlineThinking message="loading"/>);
        });
        expect(textFromTree(inline.toJSON())).toContain('loading');
        await act(async () => {
            jest.advanceTimersByTime(400);
        });
        expect(textFromTree(inline.toJSON())).toContain('loading.');
        act(() => inline.unmount());
    });

    it('truncates long footer thinking text without corrupting metrics line', async () => {
        const {render, Footer} = await load();
        const originalColumns = process.stdout.columns;

        try {
            Object.defineProperty(process.stdout, 'columns', {value: 80, configurable: true});
            const frame = render(
                <Footer
                    deploymentMode="local-cli"
                    isOperationRunning
                    isInputPaused={false}
                    thinkingStatus={{
                        active: true,
                        context: 'tool_execution',
                        message: 'This is a very long thinking status message that should not wrap',
                        taskTitle: 'Long task title',
                    }}
                    operationMetrics={{tokens: 123, cost: 0}}
                />
            ).lastFrame();

            expect(frame).toContain('Long task title');
            expect(frame).toContain('123 tokens');
            expect(frame).toContain('[ESC] Kill Switch');
            expect(frame).not.toContain('should not wrap');
        } finally {
            Object.defineProperty(process.stdout, 'columns', {value: originalColumns, configurable: true});
        }
    });

    it('omits ETA when duration or progress cannot produce a valid estimate', async () => {
        const {render, Footer} = await load();

        const zeroProgressFrame = render(
            <Footer
                isOperationRunning
                isInputPaused={false}
                operationMetrics={{
                    duration: '10s',
                    progressPercent: 0,
                }}
            />
        ).lastFrame();
        expect(zeroProgressFrame).not.toContain('ETA ');

        const doneProgressFrame = render(
            <Footer
                isOperationRunning
                isInputPaused={false}
                operationMetrics={{
                    duration: '10s',
                    progressPercent: 100,
                }}
            />
        ).lastFrame();
        expect(doneProgressFrame).not.toContain('ETA ');

        const invalidDurationFrame = render(
            <Footer
                isOperationRunning
                isInputPaused={false}
                operationMetrics={{
                    duration: 'unknown',
                    progressPercent: 50,
                }}
            />
        ).lastFrame();
        expect(invalidDurationFrame).not.toContain('ETA ');
    });

    it.each([
        ['10s', 'ETA 20s'],
        ['1m', 'ETA 2m'],
        ['1h', 'ETA 2h'],
        ['5m 20s', 'ETA 10m 40s'],
        ['1h 5m 20s', 'ETA 2h 10m'],
    ])('parses duration %s when estimating ETA', async (duration, expectedEta) => {
        const {render, Footer} = await load();

        const frame = render(
            <Footer
                isOperationRunning
                isInputPaused={false}
                operationMetrics={{
                    duration,
                    progressPercent: 50,
                }}
            />
        ).lastFrame();

        expect(frame).toContain(duration);
        expect(frame).toContain(expectedEta);
    });

    it.each([
        ['connecting'],
        ['offline'],
    ] as const)('renders footer connection state %s', async (connectionStatus) => {
        const {render, Footer} = await load();

        const frame = render(
            <Footer
                deploymentMode="single-container"
                isOperationRunning={false}
                isInputPaused
                connectionStatus={connectionStatus}
            />
        ).lastFrame();

        expect(frame).toContain('single-container');
        expect(frame).toContain('[ESC] Kill Switch');
    });

});
