import React from 'react';
import {EventEmitter} from 'events';
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

jest.unstable_mockModule('../../../src/hooks/useTerminalSize.js', () => ({
    useTerminalSize: () => ({
        availableWidth: 100,
        availableHeight: 30,
        columns: 100,
        rows: 30,
    }),
}));

const load = async () => {
    const {
        Terminal,
        buildTrimmedReportContent,
        estimateDisplayEventBytes,
        trimDisplayEventForMemory,
    } = await import('../../../src/components/Terminal.js');
    return {Terminal, buildTrimmedReportContent, estimateDisplayEventBytes, trimDisplayEventForMemory};
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
        delete process.env.CYBER_MAX_FINAL_REPORT_EVENTS;
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

    it('trims single-line report content by character budget', async () => {
        const {buildTrimmedReportContent} = await load();
        const trimmed = buildTrimmedReportContent('x'.repeat(50000));

        expect(trimmed.length).toBeLessThan(50000);
        expect(trimmed).toContain('content trimmed due to memory budget');
    });

    it('estimates and trims large nested event payloads', async () => {
        const {estimateDisplayEventBytes, trimDisplayEventForMemory} = await load();
        const event = {
            type: 'tool_start',
            tool_input: {
                command: 'scan',
                payload: 'x'.repeat(20000),
            },
            metadata: {
                output: 'y'.repeat(20000),
            },
        } as any;

        expect(estimateDisplayEventBytes(event)).toBeGreaterThan(30000);

        const trimmed = trimDisplayEventForMemory(event) as any;
        expect(trimmed.tool_input.omitted).toBe(true);
        expect(trimmed.metadata.omitted).toBe(true);
        expect(estimateDisplayEventBytes(trimmed)).toBeLessThan(estimateDisplayEventBytes(event));
    });

    it('subscribes to execution events, emits metrics, renders processed events, and cleans up', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onEvent = jest.fn();
        const onMetricsUpdate = jest.fn();
        const cleanupRef = {current: null as null | (() => void)};

        let view!: ReactTestRenderer;
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
                metrics: {tokens: 12, cost: 0.02, duration: '3s', memoryOps: 1, evidence: 2, progressPercent: 5},
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
            progressPercent: 5,
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

    it('publishes only valid progress health snapshots to the application', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onHealthUpdate = jest.fn();
        const health = {status: 'available', score: 0.84, band: 'good', health_version: '1'};

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-health"
                    terminalWidth={90}
                    onHealthUpdate={onHealthUpdate}
                    animationsEnabled={false}
                />
            );
        });

        await act(async () => {
            service.emit('event', {type: 'progress_update', step: 1, health});
            service.emit('event', {
                type: 'progress_update',
                step: 2,
                health: {status: 'unavailable', score: 0.7, band: 'degraded'},
            });
            service.emit('event', {
                type: 'progress_update',
                step: 3,
                health: {status: 'available', score: 2, band: 'excellent'},
            });
            service.emit('event', {type: 'progress_update', step: 4});
            await Promise.resolve();
        });

        expect(onHealthUpdate).toHaveBeenCalledTimes(1);
        expect(onHealthUpdate).toHaveBeenCalledWith(health);

        act(() => view.unmount());
    });

    it('renders nothing when collapsed or without a service', async () => {
        const {Terminal} = await load();
        let view!: ReactTestRenderer;

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

    it('shows an initial thinking spinner before backend events arrive', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onThinkingUpdate = jest.fn();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-initial-spinner"
                    terminalWidth={90}
                    onThinkingUpdate={onThinkingUpdate}
                    animationsEnabled
                />
            );
            await Promise.resolve();
        });
        act(() => {
            jest.advanceTimersByTime(25);
        });

        const text = textFromTree(view.toJSON());
        expect(text).not.toContain('spinner:dots');
        expect(onThinkingUpdate).toHaveBeenCalledWith(expect.objectContaining({
            active: true,
            context: 'startup',
        }));

        act(() => {
            view.unmount();
        });
    });

    it('keeps the thinking spinner visible after metrics and progress updates', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onThinkingUpdate = jest.fn();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-spinner"
                    terminalWidth={90}
                    onThinkingUpdate={onThinkingUpdate}
                    animationsEnabled
                />
            );
        });

        await act(async () => {
            service.emit('event', {type: 'operation_init', operation_id: 'run-spinner', target: 'example.com'});
            service.emit('event', {type: 'metrics_update', metrics: {duration: 1, progressPercent: 2}});
            service.emit('event', {type: 'progress_update', step: 1, progressPercent: 5});
            jest.advanceTimersByTime(50);
            await Promise.resolve();
        });

        const text = textFromTree(view.toJSON());
        expect(text).not.toContain('spinner:dots');
        expect(onThinkingUpdate).toHaveBeenCalledWith(expect.objectContaining({
            active: true,
            context: 'waiting',
        }));
        expect(onThinkingUpdate).toHaveBeenCalledWith(expect.objectContaining({
            active: true,
            context: 'tool_preparation',
        }));

        act(() => {
            view.unmount();
        });
    });

    it('clears completion cleanup timers when unmounted before delayed pruning runs', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const setTimeoutSpy = jest.spyOn(global, 'setTimeout');
        const clearTimeoutSpy = jest.spyOn(global, 'clearTimeout');

        let view!: ReactTestRenderer;
        try {
            await act(async () => {
                view = TestRenderer.create(
                    <Terminal
                        executionService={service as any}
                        sessionId="run-complete-cleanup"
                        terminalWidth={90}
                        animationsEnabled
                    />
                );
            });

            await act(async () => {
                service.emit('event', {type: 'operation_complete', metrics: {tokens: 1}});
                await Promise.resolve();
            });
            const completionTimerIndex = setTimeoutSpy.mock.calls.findIndex(call => call[1] === 1000);
            expect(completionTimerIndex).toBeGreaterThanOrEqual(0);
            const completionTimer = setTimeoutSpy.mock.results[completionTimerIndex]?.value;

            act(() => {
                view.unmount();
            });

            expect(clearTimeoutSpy).toHaveBeenCalledWith(completionTimer);
        } finally {
            setTimeoutSpy.mockRestore();
            clearTimeoutSpy.mockRestore();
        }
    });

    it('renders tool boundaries without waiting for the completed-stream timer', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-immediate-tool"
                    terminalWidth={90}
                    animationsEnabled={false}
                />
            );
        });

        await act(async () => {
            service.emit('event', {type: 'operation_init', operation_id: 'op-immediate', target: 'example.com'});
            service.emit('event', {type: 'progress_update', step: 1, progressPercent: 5});
            service.emit('event', {
                type: 'tool_start',
                tool_id: 'tool-immediate',
                tool_name: 'shell',
                tool_input: {command: 'curl http://example.com/ping'},
            });
            await Promise.resolve();
        });

        const text = textFromTree(view.toJSON());
        expect(text).toContain('[BUDGET 5%]');
        expect(text).toContain('curl http://example.com/ping');

        act(() => {
            view.unmount();
        });
    });

    it('preserves compact health data on progress events', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-health"
                    terminalWidth={90}
                    animationsEnabled={false}
                />
            );
        });

        await act(async () => {
            service.emit('event', {
                type: 'progress_update',
                step: 1,
                progressPercent: 30,
                health: {
                    status: 'available',
                    score: 0.82,
                    band: 'good',
                    failure_count: 2,
                },
            });
            await Promise.resolve();
        });

        const text = textFromTree(view.toJSON());
        expect(text).toContain('💚 82% GOOD');
        expect(text).not.toContain('failure_count');

        act(() => {
            view.unmount();
        });
    });

    it('bounds final report output events while the report cluster is active', async () => {
        process.env.CYBER_MAX_FINAL_REPORT_EVENTS = '3';
        const {Terminal} = await load();
        const service = new MockExecutionService();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-final-report-cap"
                    terminalWidth={90}
                    animationsEnabled={false}
                />
            );
        });

        await act(async () => {
            service.emit('event', {
                type: 'progress_update',
                step: 'FINAL REPORT',
                operation_stage: 'final_report',
            });
            for (let index = 0; index < 5; index += 1) {
                service.emit('event', {type: 'output', content: `final-output-${index}`});
            }
            jest.advanceTimersByTime(100);
            await Promise.resolve();
        });

        const text = textFromTree(view.toJSON());
        expect(text).not.toContain('final-output-0');
        expect(text).not.toContain('final-output-1');
        expect(text).toContain('final-output-2');
        expect(text).toContain('final-output-4');

        act(() => {
            view.unmount();
        });
    });

    it('keeps prior tool results visible after model and later tool transitions', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-tool-output-commit"
                    terminalWidth={90}
                    animationsEnabled={false}
                />
            );
        });

        await act(async () => {
            service.emit('event', {type: 'operation_init', operation_id: 'op-tools', target: 'example.com'});
            service.emit('event', {type: 'progress_update', step: 1});
            service.emit('event', {type: 'tool_start', tool_id: 'tool-1', tool_name: 'shell'});
            service.emit('event', {
                type: 'output',
                content: 'first tool result',
                metadata: {fromToolBuffer: true, tool: 'shell'},
            });
            service.emit('event', {type: 'model_invocation_start'});
            service.emit('event', {type: 'reasoning', content: 'thinking about the next tool'});
            service.emit('event', {type: 'tool_start', tool_id: 'tool-2', tool_name: 'shell'});
            service.emit('event', {
                type: 'output',
                content: 'second tool result',
                metadata: {fromToolBuffer: true, tool: 'shell'},
            });
            service.emit('event', {type: 'tool_end', toolId: 'tool-2', toolName: 'shell'});
            jest.advanceTimersByTime(100);
            await Promise.resolve();
        });

        const text = textFromTree(view.toJSON());
        expect(text).toContain('first tool result');
        expect(text).toContain('second tool result');

        act(() => {
            view.unmount();
        });
    });

    it('renders standardized tool_output stdout through the terminal stream', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-tool-output-stdout"
                    terminalWidth={90}
                    animationsEnabled={false}
                />
            );
        });

        await act(async () => {
            service.emit('event', {type: 'operation_init', operation_id: 'op-stdout', target: 'example.com'});
            service.emit('event', {type: 'tool_output', tool_name: 'shell', status: 'success', output: {stdout: 'visible stdout'}});
            jest.advanceTimersByTime(100);
            await Promise.resolve();
        });

        expect(textFromTree(view.toJSON())).toContain('visible stdout');

        act(() => {
            view.unmount();
        });
    });

    it('routes backend thinking events to footer status', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onThinkingUpdate = jest.fn();
        const startTime = Date.now() - 5000;

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-task-spinner"
                    terminalWidth={90}
                    onThinkingUpdate={onThinkingUpdate}
                    animationsEnabled
                />
            );
        });

        await act(async () => {
            service.emit('event', {
                type: 'thinking',
                context: 'startup',
                message: 'Booting',
                startTime,
            });
            service.emit('event', {type: 'thinking_end'});
            jest.advanceTimersByTime(50);
            await Promise.resolve();
        });

        const text = textFromTree(view.toJSON());
        expect(text).not.toContain('spinner:dots');
        expect(onThinkingUpdate).toHaveBeenCalledWith({
            active: true,
            context: 'startup',
            message: 'Booting',
            startTime,
        });
        expect(onThinkingUpdate).toHaveBeenCalledWith({active: false});

        act(() => {
            view.unmount();
        });
    });

    it('routes workflow activity lifecycle to the footer thinking status', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onThinkingUpdate = jest.fn();
        let view!: ReactTestRenderer;

        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-workflow-activity-spinner"
                    terminalWidth={90}
                    onThinkingUpdate={onThinkingUpdate}
                    animationsEnabled
                />
            );
        });

        await act(async () => {
            service.emit('event', {
                type: 'workflow_activity',
                role: 'task_creator',
                action: 'task_create_prompt',
                label: 'Task creation',
                status: 'started',
                attempt: 1,
                attempt_total: 2,
            });
            await Promise.resolve();
        });

        expect(onThinkingUpdate).toHaveBeenCalledWith(expect.objectContaining({
            active: true,
            context: 'tool_preparation',
            message: 'Task creation',
        }));

        await act(async () => {
            service.emit('event', {
                type: 'workflow_activity',
                role: 'task_creator',
                action: 'task_create_prompt',
                label: 'Task creation',
                status: 'completed',
                attempt: 1,
                attempt_total: 2,
            });
            await Promise.resolve();
        });

        expect(onThinkingUpdate).toHaveBeenLastCalledWith({active: false});
        act(() => view.unmount());
    });

    it('keeps the footer active until all workflow activities terminate', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onThinkingUpdate = jest.fn();
        let view!: ReactTestRenderer;

        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-workflow-activity-overlap"
                    onThinkingUpdate={onThinkingUpdate}
                />
            );
        });

        await act(async () => {
            service.emit('event', {type: 'workflow_activity', role: 'plan_creator', status: 'started', attempt: 1});
            service.emit('event', {type: 'workflow_activity', role: 'plan_critic', status: 'started', attempt: 1});
            service.emit('event', {type: 'workflow_activity', role: 'plan_creator', status: 'completed', attempt: 1});
            await Promise.resolve();
        });

        expect(onThinkingUpdate).not.toHaveBeenLastCalledWith({active: false});

        await act(async () => {
            service.emit('event', {type: 'workflow_activity', role: 'plan_critic', status: 'failed', attempt: 1});
            await Promise.resolve();
        });
        expect(onThinkingUpdate).toHaveBeenLastCalledWith({active: false});
        act(() => view.unmount());
    });

    it('uses final report progress labels as thinking task titles', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onThinkingUpdate = jest.fn();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-report-thinking-title"
                    terminalWidth={90}
                    onThinkingUpdate={onThinkingUpdate}
                    animationsEnabled
                />
            );
        });

        await act(async () => {
            service.emit('event', {
                type: 'progress_update',
                step: 'REPORT_AGENT',
                operation_stage: 'final_report',
                report_step_index: 1,
                report_step_total: 2,
                report_step_label: 'Executive summary',
            });
            service.emit('event', {
                type: 'progress_update',
                step: 'REPORT_AGENT',
                operation_stage: 'final_report',
                report_step_index: 2,
                report_step_total: 2,
            });
            jest.advanceTimersByTime(18_500);
            await Promise.resolve();
        });

        expect(onThinkingUpdate).toHaveBeenCalledWith(expect.objectContaining({
            active: true,
            context: 'waiting',
            message: 'Generating report',
            taskTitle: 'Executive summary',
        }));
        expect(onThinkingUpdate).toHaveBeenCalledWith(expect.objectContaining({
            active: true,
            context: 'waiting',
            message: 'Generating report',
        }));

        const unlabeledCall = onThinkingUpdate.mock.calls
            .map(call => call[0])
            .find(status => (
                status.active === true &&
                status.message === 'Generating report' &&
                status.taskTitle === undefined
            ));
        expect(unlabeledCall).toEqual(expect.objectContaining({
            active: true,
            context: 'waiting',
            message: 'Generating report',
        }));

        act(() => {
            view.unmount();
        });
    });

    it('uses Ragas progress labels as evaluation thinking task titles', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onThinkingUpdate = jest.fn();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-ragas-thinking-title"
                    terminalWidth={90}
                    onThinkingUpdate={onThinkingUpdate}
                    animationsEnabled
                />
            );
        });

        await act(async () => {
            service.emit('event', {
                type: 'progress_update',
                step: 'RAGAS_PREPARATION',
                operation_stage: 'ragas_evaluation',
                evaluation_step_kind: 'reference_topics',
                evaluation_step_label: 'Operation: Generate Reference Topics',
            });
            service.emit('event', {
                type: 'evaluation_step_complete',
                operation_id: 'OP_TEST',
                operation_stage: 'ragas_evaluation',
                evaluation_scope: 'operation',
                evaluation_step_kind: 'rubric_judge',
                status: 'skipped',
                message: 'Insufficient evidence for rubric judging',
            });
            await Promise.resolve();
        });

        expect(onThinkingUpdate).toHaveBeenCalledWith(expect.objectContaining({
            active: true,
            context: 'waiting',
            message: 'Evaluating assessment',
            taskTitle: 'Operation: Generate Reference Topics',
        }));
        const lastActiveStatus = onThinkingUpdate.mock.calls
            .map(call => call[0])
            .filter(status => status.active)
            .at(-1);
        expect(lastActiveStatus).toEqual(expect.objectContaining({
            context: 'waiting',
            message: 'Evaluating assessment',
            taskTitle: 'Operation: Generate Reference Topics',
        }));
        expect(textFromTree(view.toJSON())).toContain(
            '[RAGAS EVALUATION PREPARING] Operation: Generate Reference Topics'
        );
        expect(textFromTree(view.toJSON())).toContain('operation: rubric judge skipped');

        await act(async () => {
            service.emit('event', {
                type: 'evaluation_complete',
                status: 'completed',
                success: true,
                scores: {'operation/evidence_quality': 0.75},
                average_score: 0.75,
            });
            await Promise.resolve();
        });
        expect(onThinkingUpdate).toHaveBeenLastCalledWith({active: false});
        expect(textFromTree(view.toJSON())).toContain('Average: 75.0%');
        expect(textFromTree(view.toJSON())).not.toContain('tool: evaluation');

        act(() => {
            view.unmount();
        });
    });

    it('processes uncommon event transitions without duplicating or crashing', async () => {
        const {Terminal} = await load();
        const service = new MockExecutionService();
        const onEvent = jest.fn();

        let view!: ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <Terminal
                    executionService={service as any}
                    sessionId="run-branches"
                    terminalWidth={100}
                    onEvent={onEvent}
                    animationsEnabled
                />
            );
        });

        await act(async () => {
            service.emit('event', {type: 'output', content: 'booting'});
            jest.advanceTimersByTime(200);
            service.emit('event', {type: 'operation_init', operation_id: 'op-2', target: 'example.com'});
            service.emit('event', {
                type: 'progress_update',
                step: 1,
                agent_name: 'web_tester',
                agent_sub_step: 1,
                agent_total_actions: 2,
            });
            service.emit('event', {type: 'reasoning', content: ' First thought ', agent_name: 'web_tester'});
            jest.advanceTimersByTime(20);
            service.emit('event', {type: 'thinking', context: 'waiting', startTime: Date.now(), metadata: {phase: 'x'}});
            service.emit('event', {type: 'thinking_end'});
            service.emit('event', {type: 'delayed_thinking_start', context: 'tool_execution'});
            service.emit('event', {
                type: 'tool_start',
                timestamp: new Date().toISOString(),
                tool_name: 'handoff_to_agent',
                tool_input: {agent_name: 'auth_agent'},
                agent_name: 'web_tester',
            });
            service.emit('event', {type: 'tool_input_update', tool_id: 'tool-1', tool_input: {command: 'whoami'}});
            service.emit('event', {type: 'tool_input_corrected', toolId: 'tool-1', tool_input: {command: 'id'}});
            service.emit('event', {type: 'shell_command', command: 'id'});
            service.emit('event', {type: 'output', content: '', metadata: {fromToolBuffer: true, tool: 'shell'}});
            service.emit('event', {type: 'output', content: 'uid=1000', metadata: {fromToolBuffer: true, tool: 'shell'}});
            service.emit('event', {type: 'output', content: 'uid=1000', metadata: {fromToolBuffer: true, tool: 'shell'}});
            service.emit('event', {type: 'tool_invocation_end'});
            service.emit('event', {type: 'model_invocation_start'});
            service.emit('event', {type: 'model_stream_delta', delta: 'ignored'});
            service.emit('event', {type: 'reasoning_delta', delta: 'ignored'});
            service.emit('event', {type: 'prompt_change', action: 'compact'});
            service.emit('event', {type: 'output', content: 'output\nreal content'});
            service.emit('event', {type: 'output', content: 'output'});
            service.emit('event', {type: 'output', content: 'Report saved to: /tmp/report.md'});
            service.emit('event', {type: 'output', content: '# SECURITY ASSESSMENT REPORT\nBody'});
            service.emit('event', {type: 'progress_update', step: 'FINAL REPORT'});
            service.emit('event', {
                type: 'progress_update',
                step: 'REPORT_AGENT',
                operation_stage: 'final_report',
                report_step_index: 1,
                report_step_total: 2,
                report_step_label: 'Executive summary',
            });
            service.emit('event', {
                type: 'progress_update',
                step: 'REPORT_AGENT',
                operation_stage: 'final_report',
                report_step_index: 2,
                report_step_total: 2,
                report_step_label: 'Assessment methodology',
            });
            service.emit('event', {type: 'report_content', content: '# SECURITY ASSESSMENT REPORT\nFinal body'});
            service.emit('event', {
                type: 'progress_update',
                step: 'RAGAS_PREPARATION',
                operation_stage: 'ragas_evaluation',
                evaluation_step_kind: 'reference_topics',
                evaluation_step_label: 'Operation: Generate Reference Topics',
            });
            service.emit('event', {
                type: 'progress_update',
                step: 'RAGAS_METRIC',
                operation_stage: 'ragas_evaluation',
                evaluation_step_index: 1,
                evaluation_step_total: 5,
                evaluation_step_kind: 'metric',
                evaluation_step_label: 'Operation: Evidence Quality',
            });
            service.emit('event', {type: 'assessment_complete', success: false});
            service.emit('event', {type: 'termination_reason', reason: 'user_stopped', message: 'Stopped'});
            service.emit('event', {type: 'output', content: 'Assessment stopped by user'});
            service.emit('event', {type: 'output', content: 'meaningful line after stop'});
            jest.advanceTimersByTime(500);
            await Promise.resolve();
        });

        const text = textFromTree(view.toJSON());
        expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({type: 'operation_init'}));
        expect(text).toContain('[AGENT: WEB TESTER');
        expect(text).toContain('SECURITY ASSESSMENT REPORT');
        expect((text.match(/\[FINAL REPORT\]/g) || []).length).toBe(1);
        expect(text).toContain('[FINAL REPORT 1/2] Executive summary');
        expect(text).toContain('[FINAL REPORT 2/2] Assessment methodology');
        expect(text).toContain('[RAGAS EVALUATION PREPARING] Operation: Generate Reference Topics');
        expect(text).toContain('[RAGAS EVALUATION 1/5] Operation: Evidence Quality');
        expect((text.match(/\[FINAL REPORT 1\/2\] Executive summary/g) || []).length).toBe(1);
        expect((text.match(/\[FINAL REPORT 2\/2\] Assessment methodology/g) || []).length).toBe(1);
        expect((text.match(/\[RAGAS EVALUATION PREPARING\]/g) || []).length).toBe(1);
        expect((text.match(/\[RAGAS EVALUATION 1\/5\]/g) || []).length).toBe(1);
        expect(text.indexOf('[FINAL REPORT 2/2]')).toBeLessThan(text.indexOf('[RAGAS EVALUATION PREPARING]'));
        expect(text.indexOf('[RAGAS EVALUATION PREPARING]')).toBeLessThan(text.indexOf('[RAGAS EVALUATION 1/5]'));
        expect(text).toContain('TERMINATED: Stopped');

        act(() => {
            view.unmount();
        });
    });
});
