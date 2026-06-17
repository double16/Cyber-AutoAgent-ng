import React from 'react';
import {EventEmitter} from 'events';
import {TextDecoder, TextEncoder} from 'util';
import {jest} from '@jest/globals';
import TestRenderer, {act} from 'react-test-renderer';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

jest.unstable_mockModule('ink-spinner', () => ({
    default: ({type}: { type?: string }) => <span>spinner:{type}</span>,
}));

jest.unstable_mockModule('../../../src/hooks/useTerminalSize.js', () => ({
    useTerminalSize: () => ({
        availableWidth: 100,
        availableHeight: 30,
        columns: 100,
        rows: 30,
    }),
}));

const load = async () => {
    const {Terminal, buildTrimmedReportContent} = await import('../../../src/components/Terminal.js');
    return {Terminal, buildTrimmedReportContent};
};

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

class MockExecutionService extends EventEmitter {
    getMode = jest.fn(() => 'local-cli');
}

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('Terminal event processing', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        delete process.env.CYBER_TEST_MODE;
        delete process.env.CYBER_HEAP_SOFT_LIMIT_MB;
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('trims long report content while preserving head and tail', async () => {
        const {buildTrimmedReportContent} = await load();
        const short = 'one\ntwo';
        expect(buildTrimmedReportContent(short)).toBe(short);

        const long = Array.from({length: 150}, (_, index) => `line-${index}`).join('\n');
        const trimmed = buildTrimmedReportContent(long);
        expect(trimmed).toContain('line-0');
        expect(trimmed).toContain('... (content continues)');
        expect(trimmed).toContain('line-149');
    });

    it('subscribes to execution events, emits metrics, renders processed events, and cleans up', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onEvent = jest.fn();
        const onMetricsUpdate = jest.fn();
        const cleanupRef = {current: null as null | (() => void)};

        let view!: TestRenderer.ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-1"
                    terminalWidth={90}
                    onEvent={onEvent}
                    onMetricsUpdate={onMetricsUpdate}
                    animationsEnabled
                    cleanupRef={cleanupRef}
                />
            );
        });

        expect(service.listenerCount('event')).toBe(1);
        expect(cleanupRef.current).toEqual(expect.any(Function));

        await act(async () => {
            service.emit('event', {
                type: 'metrics_update',
                metrics: {tokens: 12, cost: 0.02, duration: '3s', memoryOps: 1, evidence: 2},
            });
            service.emit('event', {
                type: 'operation_init',
                operation_id: 'run-1',
                module: 'web',
                target: 'example.com'
            });
            service.emit('event', {type: 'step_start', step: 1, description: 'Scan target'});
            service.emit('event', {type: 'tool_start', tool_id: 'tool-1', tool_name: 'nmap', category: 'network'});
            service.emit('event', {
                type: 'output',
                content: 'port 80 open',
                metadata: {fromToolBuffer: true, tool: 'nmap'},
            });
            service.emit('event', {type: 'rate_limit', wait_total: 4, message: 'slow down'});
            service.emit('event', {type: 'report_start', title: 'Final Report'});
            service.emit('event', {type: 'report_content', content: '# Finding\nDetails'});
            service.emit('event', {type: 'assessment_complete', success: true});
            jest.advanceTimersByTime(250);
            await Promise.resolve();
        });

        expect(onMetricsUpdate).toHaveBeenCalledWith({
            tokens: 12,
            cost: 0.02,
            duration: '3s',
            memoryOps: 1,
            evidence: 2,
        });
        expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({type: 'metrics_update'}));
        expect(textFromTree(view.toJSON())).toContain('SECURITY ASSESSMENT REPORT');

        act(() => {
            cleanupRef.current?.();
            service.emit('complete');
            service.emit('stopped');
            view.update(
                <Terminal
                    executionService={service as any}
                    sessionId="run-2"
                    terminalWidth={90}
                    onEvent={onEvent}
                    animationsEnabled={false}
                    cleanupRef={cleanupRef}
                />
            );
        });

        act(() => {
            view.unmount();
        });
        expect(service.listenerCount('event')).toBe(0);
        expect(cleanupRef.current).toBeNull();
    });

    it('renders nothing when collapsed or without a service', async () => {
        const {Terminal} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(
                <Terminal executionService={null} sessionId="empty" collapsed/>
            );
        });
        expect(view.toJSON()).toBeNull();

        act(() => {
            view.update(<Terminal executionService={null} sessionId="empty"/>);
        });
        expect(textFromTree(view.toJSON())).toBe('');
    });
});
