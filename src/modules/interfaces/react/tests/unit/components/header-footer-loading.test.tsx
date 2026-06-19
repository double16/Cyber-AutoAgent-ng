import React from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {jest} from '@jest/globals';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

jest.unstable_mockModule('ink-spinner', () => ({
    default: ({type}: { type?: string }) => <span>spinner:{type}</span>,
}));

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
    useConfig: () => ({
        config: {
            deploymentMode: 'local-cli',
            modelProvider: 'bedrock',
        },
    }),
}));

const load = async () => {
    const [{render}, {Header}, {Footer}, {LoadingIndicator}] = await Promise.all([
        import('ink-testing-library'),
        import('../../../src/components/Header.js'),
        import('../../../src/components/Footer.js'),
        import('../../../src/components/LoadingIndicator.js'),
    ]);

    return {render, Header, Footer, LoadingIndicator};
};

describe('header, footer, and loading components', () => {
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
            Object.defineProperty(process.stdout, 'columns', {value: 120, configurable: true});

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
                        duration: '10s',
                        memoryOps: 3,
                    }}
                />
            ).lastFrame();

            expect(frame).toContain('local-cli');
            expect(frame).toContain('12,345 tokens');
            expect(frame).toContain('&lt;$0.01');
            expect(frame).toContain('10s');
            expect(frame).toContain('3 mem');
            expect(frame).toContain('2 errors');

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

    it('renders loading indicator phases, custom text, and dot animation', async () => {
        const {render, LoadingIndicator} = await load();

        const phased = render(<LoadingIndicator spinnerType="line"/>);
        expect(phased.lastFrame()).toContain('Analyzing security posture');
        expect(phased.lastFrame()).toContain('spinner:line');

        const fixed = render(<LoadingIndicator showPhases={false} text="Waiting" color="green"/>);
        expect(fixed.lastFrame()).toContain('Waiting');
    });
});
