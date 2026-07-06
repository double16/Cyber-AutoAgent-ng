import React from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
    useConfig: () => ({
        config: {
            deploymentMode: 'local-cli',
            modelProvider: 'bedrock',
        },
    }),
}));

const load = async () => {
    const [{render}, {Header}, {Footer}] = await Promise.all([
        import('ink-testing-library'),
        import('../../../src/components/Header.js'),
        import('../../../src/components/Footer.js'),
    ]);

    return {render, Header, Footer};
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
