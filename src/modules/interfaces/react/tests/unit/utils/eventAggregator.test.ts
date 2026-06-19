import {jest} from '@jest/globals';
import {EventAggregator} from '../../../src/utils/eventAggregator.js';

describe('EventAggregator', () => {
    it('reports no pending events for the simplified buffering API', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.hasPendingEvents()).toBe(false);
        expect(aggregator.flushPendingEvents()).toEqual([]);
        expect(aggregator.flush()).toEqual([]);
    });

    it('buffers step headers until the first tool signal', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({
            type: 'step_header',
            step: 1,
            maxSteps: 3,
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
                type: 'step_header',
                step: 1,
                maxSteps: 3,
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

    it('keeps reasoning attached before pending step headers and ends active thinking', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({
            type: 'thinking',
            context: 'startup',
            startTime: 1,
        })).toEqual([
            expect.objectContaining({type: 'thinking', context: 'startup', startTime: 1}),
        ]);

        aggregator.processEvent({type: 'step_header', step: 2, maxSteps: 5});
        const reasoning = aggregator.processEvent({
            type: 'reasoning',
            content: '  analyzed prior output  ',
        });

        expect(reasoning).toEqual([
            {type: 'thinking_end'},
            {type: 'reasoning', content: 'analyzed prior output', swarm_agent: undefined},
        ]);

        const toolStart = aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 't2'});
        expect(toolStart[0]).toEqual(expect.objectContaining({type: 'step_header', step: 2}));
    });

    it('deduplicates tool starts by step and tool id until tool end cleanup', () => {
        const aggregator = new EventAggregator();

        aggregator.processEvent({type: 'step_header', step: 1});
        expect(aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 'dup'}))
            .toEqual(expect.arrayContaining([expect.objectContaining({type: 'tool_start', toolId: 'dup'})]));

        expect(aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 'dup'})).toEqual([]);

        aggregator.processEvent({type: 'tool_end', toolId: 'dup', toolName: 'shell'});
        expect(aggregator.processEvent({type: 'tool_start', tool_name: 'shell', toolId: 'dup'}))
            .toEqual([expect.objectContaining({type: 'tool_start', toolId: 'dup'})]);
    });

    it('flushes pending step headers for shell_command and starts delayed thinking', () => {
        const nowSpy = jest.spyOn(Date, 'now').mockReturnValue(12345);

        try {
            const aggregator = new EventAggregator();
            aggregator.processEvent({type: 'step_header', step: 3});
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

    it('transforms handoff_to_agent tool starts during swarm operations', () => {
        const aggregator = new EventAggregator();

        aggregator.processEvent({
            type: 'swarm_start',
            agent_names: ['recon'],
        });

        const events = aggregator.processEvent({
            type: 'tool_start',
            tool_name: 'handoff_to_agent',
            tool_input: {
                agent_name: 'auth',
                message: 'check login',
                context: {target: 'example.com'},
            },
            toolId: 'handoff-1',
            timestamp: 99,
        });

        expect(events).toEqual([
            expect.objectContaining({
                type: 'swarm_handoff',
                from_agent: 'recon',
                to_agent: 'auth',
                message: 'check login',
                shared_context: {target: 'example.com'},
                timestamp: 99,
                sequence: 1,
            }),
            expect.objectContaining({
                type: 'tool_start',
                tool_name: 'handoff_to_agent',
                _handoff_processed: true,
            }),
        ]);

        expect(aggregator.processEvent({type: 'reasoning', content: 'now auth works'})).toEqual([
            expect.objectContaining({
                type: 'reasoning',
                content: 'now auth works',
                swarm_agent: 'auth',
            }),
        ]);
    });

    it('handles swarm event pass-through and reset cases', () => {
        const aggregator = new EventAggregator();

        expect(aggregator.processEvent({type: 'swarm_handoff'})).toEqual([]);
        expect(aggregator.processEvent({type: 'swarm_handoff', to_agent: 'auth', message: 'go'})).toEqual([
            {type: 'swarm_handoff', to_agent: 'auth', message: 'go'},
        ]);
        expect(aggregator.processEvent({type: 'swarm_complete', status: 'done'})).toEqual([
            {type: 'swarm_complete', status: 'done'},
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
