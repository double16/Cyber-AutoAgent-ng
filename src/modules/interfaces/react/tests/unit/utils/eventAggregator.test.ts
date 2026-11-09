import {describe, expect, it, jest} from '@jest/globals';
import {EventAggregator} from '../../../src/utils/eventAggregator.js';

describe('EventAggregator', () => {
    it('reports no pending events for the simplified buffering API', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.hasPendingEvents()).toBe(false);
        expect(aggregator.flushPendingEvents()).toEqual([]);
        expect(aggregator.flush()).toEqual([]);
    });

    it('buffers progress updates until the first tool signal', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({
            type: 'progress_update',
            step: 20,
            progressPercent: 20,
            operation: 'OP_TEST',
            duration: '0s',
        })).toEqual([]);

        const outputBeforeTool = aggregator.processEvent({type: 'output', content: 'late previous output'});
        expect(outputBeforeTool).toEqual([
            {type: 'output', content: 'late previous output', toolId: undefined},
        ]);

        const toolStart = aggregator.processEvent({
            type: 'tool_start',
            tool_name: 'shell',
            tool_input: {command: ['id']},
            toolId: 'tool-1',
        });

        expect(toolStart).toEqual([
            expect.objectContaining({
                type: 'progress_update',
                step: 20,
                progressPercent: 20,
                operation: 'OP_TEST',
            }),
            expect.objectContaining({
                type: 'tool_start',
                tool_name: 'shell',
                tool_input: {command: ['id']},
                toolId: 'tool-1',
            }),
        ]);
    });

    it('emits final report progress updates immediately', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({
            type: 'progress_update',
            step: 'REPORT_AGENT',
            operation_stage: 'final_report',
            report_step_index: 1,
            report_step_total: 3,
            report_step_kind: 'executive',
            report_step_label: 'Executive summary',
            operation: 'OP_TEST',
        })).toEqual([
            expect.objectContaining({
                type: 'progress_update',
                step: 'REPORT_AGENT',
                operation_stage: 'final_report',
                report_step_index: 1,
                report_step_total: 3,
                report_step_label: 'Executive summary',
                operation: 'OP_TEST',
            }),
        ]);
    });

    it('emits Ragas evaluation progress updates immediately', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({
            type: 'progress_update',
            step: 'RAGAS_METRIC',
            operation_stage: 'ragas_evaluation',
            evaluation_step_index: 2,
            evaluation_step_total: 5,
            evaluation_scope: 'operation',
            evaluation_metric: 'evidence_quality',
            evaluation_step_label: 'Operation: Evidence Quality',
            operation: 'OP_TEST',
        })).toEqual([
            expect.objectContaining({
                type: 'progress_update',
                operation_stage: 'ragas_evaluation',
                evaluation_step_index: 2,
                evaluation_step_total: 5,
                evaluation_scope: 'operation',
                evaluation_metric: 'evidence_quality',
                evaluation_step_label: 'Operation: Evidence Quality',
            }),
        ]);
    });

    it('preserves unindexed Ragas preparation progress', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({
            type: 'progress_update',
            step: 'RAGAS_PREPARATION',
            operation_stage: 'ragas_evaluation',
            evaluation_step_kind: 'reference_topics',
            evaluation_scope: 'operation',
            evaluation_step_label: 'Operation: Generate Reference Topics',
            operation: 'OP_TEST',
        })).toEqual([
            expect.objectContaining({
                type: 'progress_update',
                step: 'RAGAS_PREPARATION',
                operation_stage: 'ragas_evaluation',
                evaluation_step_kind: 'reference_topics',
                evaluation_scope: 'operation',
                evaluation_step_label: 'Operation: Generate Reference Topics',
            }),
        ]);
    });

    it('keeps reasoning attached before pending progress updates and ends active thinking', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({
            type: 'thinking',
            context: 'startup',
            startTime: 1,
        })).toEqual([
            expect.objectContaining({type: 'thinking', context: 'startup', startTime: 1}),
        ]);

        aggregator.processEvent({type: 'progress_update', step: 1, progressPercent: 40});
        const reasoning = aggregator.processEvent({
            type: 'reasoning',
            content: '  analyzed prior output  ',
        });

        expect(reasoning).toEqual([
            {type: 'thinking_end'},
            {
                type: 'reasoning',
                content: 'analyzed prior output',
                agent_run_id: undefined,
                agent_name: undefined,
                agent_type: undefined,
                parent_agent_run_id: undefined
            },
        ]);

        const toolStart = aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 't2'});
        expect(toolStart[0]).toEqual(expect.objectContaining({type: 'progress_update', step: 1, progressPercent: 40}));
    });

    it('deduplicates tool starts by progress boundary and tool id until tool end cleanup', () => {
        const aggregator = new EventAggregator();

        aggregator.processEvent({type: 'progress_update', step: 1, progressPercent: 20});
        expect(aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 'dup'}))
            .toEqual(expect.arrayContaining([expect.objectContaining({type: 'tool_start', toolId: 'dup'})]));

        expect(aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 'dup'})).toEqual([]);

        aggregator.processEvent({type: 'tool_end', toolId: 'dup', toolName: 'shell'});
        expect(aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 'dup'}))
            .toEqual([expect.objectContaining({type: 'tool_start', toolId: 'dup'})]);
    });

    it('preserves tool completion outcome metadata', () => {
        const aggregator = new EventAggregator();

        const events = aggregator.processEvent({
            type: 'tool_end',
            toolId: 'blocked',
            toolName: 'shell',
            success: false,
            outcome: 'blocked',
            executed: false,
        });

        expect(events).toEqual([
            expect.objectContaining({
                type: 'tool_end',
                success: false,
                outcome: 'blocked',
                executed: false,
            }),
        ]);
    });

    it('flushes pending progress updates for shell_command and starts delayed thinking', () => {
        const nowSpy = jest.spyOn(Date, 'now').mockReturnValue(12345);

        try {
            const aggregator = new EventAggregator();
            aggregator.processEvent({type: 'progress_update', step: 3, progressPercent: 60});
            aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 'shell-1'});

            const events = aggregator.processEvent({type: 'shell_command', command: 'whoami'});

            expect(events).toEqual([
                expect.objectContaining({
                    type: 'shell_command',
                    command: 'whoami',
                    toolId: 'shell-1',
                    id: 'shell_12345',
                    sessionId: 'current',
                }),
                expect.objectContaining({
                    type: 'delayed_thinking_start',
                    context: 'tool_execution',
                    startTime: 12345,
                    delay: 100,
                }),
            ]);
        } finally {
            nowSpy.mockRestore();
        }
    });

    it('deduplicates repeated output in a short window and ends thinking on output', () => {
        let now = 1000;
        const nowSpy = jest.spyOn(Date, 'now').mockImplementation(() => now);

        try {
            const aggregator = new EventAggregator();
            aggregator.processEvent({type: 'thinking', context: 'tool'});

            expect(aggregator.processEvent({type: 'output', content: 'same'})).toEqual([
                {type: 'thinking_end'},
                {type: 'output', content: 'same', toolId: undefined},
            ]);

            now += 500;
            expect(aggregator.processEvent({type: 'output', content: 'same'})).toEqual([]);

            now += 1000;
            expect(aggregator.processEvent({type: 'output', content: 'same'})).toEqual([
                {type: 'output', content: 'same', toolId: undefined},
            ]);
        } finally {
            nowSpy.mockRestore();
        }
    });

    it('passes handoff_to_agent tool starts through as normal tool events', () => {
        const aggregator = new EventAggregator();

        const events = aggregator.processEvent({
            type: 'tool_start',
            tool_name: 'handoff_to_agent',
            tool_input: {
                agent_name: 'auth',
                message: 'check login',
                context: {target: 'example.com'},
            },
            toolId: 'handoff-1',
            agent_name: 'auth',
            agent_run_id: 'auth-1',
            timestamp: 99,
        });

        expect(events).toEqual([
            expect.objectContaining({
                type: 'tool_start',
                tool_name: 'handoff_to_agent',
                toolId: 'handoff-1',
                agent_name: 'auth',
                agent_run_id: 'auth-1',
            }),
        ]);

        expect(aggregator.processEvent({
            type: 'reasoning',
            content: 'now auth works',
            agent_name: 'auth',
            agent_run_id: 'auth-1',
        })).toEqual([
            expect.objectContaining({
                type: 'reasoning',
                content: 'now auth works',
                agent_name: 'auth',
                agent_run_id: 'auth-1',
            }),
        ]);
    });

    it('emits metrics on operation_complete and passes unknown events through', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({
            type: 'operation_complete',
            metrics: {tokens: 10},
            duration: '5s',
        })).toEqual([
            {type: 'metrics_update', metrics: {tokens: 10}, duration: '5s'},
        ]);

        expect(aggregator.processEvent({type: 'custom_event', value: 1})).toEqual([
            {type: 'custom_event', value: 1},
        ]);
    });
});

it('resets operation state and handles thinking, output and tool lifecycle boundaries', () => {
  const a = new EventAggregator();
  expect(a.processEvent({type: 'thinking_end'})).toEqual([]);
  expect(a.processEvent({type: 'delayed_thinking_start'})).toEqual([{type: 'delayed_thinking_start'}]);
  expect(a.processEvent({type: 'delayed_thinking_start'})).toEqual([]);
  expect(a.processEvent({type: 'thinking_end'})).toEqual([{type: 'thinking_end'}]);
  a.processEvent({type: 'thinking'});
  const tool = a.processEvent({type: 'tool_start', toolName: 'network', args: {host: 'example'}, tool_id: 'id'});
  expect(tool[0].type).toBe('thinking_end');
  expect(tool[1]).toMatchObject({type: 'tool_start', tool_name: 'network'});
  expect(a.processEvent({type: 'tool_start', tool_id: 'id'})).toEqual([]);
  expect(a.processEvent({type: 'tool_end', toolId: 'id', toolName: 'network'}).at(-1)).toMatchObject({tool: 'network'});
  a.processEvent({type: 'thinking'});
  expect(a.processEvent({type: 'tool_end', toolId: 'unknown'})[0].type).toBe('thinking_end');
  expect(a.processEvent({type: 'operation_init'})).toEqual([]);
  expect(a.processEvent({type: 'tool_start', tool_id: 'id'})[0]).toMatchObject({tool_name: '', tool_input: {}});
  a.processEvent({type: 'progress_update', step: 'phase'});
  expect(a.processEvent({type: 'tool_output', output: 'result'}).map(e => e.type)).toEqual(['progress_update', 'tool_output']);
  a.processEvent({type: 'progress_update', step: 2});
  expect(a.processEvent({type: 'shell_command', command: 'ls'})[0].type).toBe('progress_update');
  expect(a.processEvent({type: 'output'})).toEqual([]);
  expect(a.processEvent({type: 'reasoning', content: ' '})).toEqual([]);
  expect(a.processEvent({type: 'reasoning_delta', content: 'legacy'})).toEqual([]);
  expect(a.processEvent({type: 'operation_complete'})[0]).toMatchObject({metrics: {}});
  for (const type of ['operation_terminated', 'operation_finalized']) expect(a.processEvent({type})).toEqual([{type}]);
});
