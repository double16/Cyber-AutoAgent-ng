import {describe, expect, it} from '@jest/globals';

type EventType =
    | 'operation_init'
    | 'reasoning'
    | 'progress_update'
    | 'tool_start'
    | 'output'
    | 'tool_end'
    | 'tool_invocation_end'
    | 'thinking'
    | 'rate_limit'
    | 'operation_complete';

interface TestEvent {
    type: EventType;
    content?: string;
    tool_name?: string;
    step?: number;
    context?: string;
}

function processEventsForSpinners(events: TestEvent[], animationsEnabled = true): TestEvent[] {
    const results: TestEvent[] = [];
    let activeThinking = false;
    let activeReasoning = false;

    const showThinking = (context: string) => {
        activeThinking = true;
        results.push({type: 'thinking', context});
    };

    for (const event of events) {
        switch (event.type) {
            case 'operation_init':
                results.push(event);
                if (animationsEnabled && !activeThinking) showThinking('startup');
                break;
            case 'progress_update':
                results.push(event);
                if (animationsEnabled) showThinking('tool_preparation');
                break;
            case 'reasoning':
                activeThinking = false;
                activeReasoning = true;
                results.push(event);
                break;
            case 'tool_start':
                if (animationsEnabled) showThinking('tool_execution');
                results.push(event);
                break;
            case 'output':
                activeThinking = false;
                activeReasoning = false;
                results.push(event);
                break;
            case 'tool_end':
            case 'tool_invocation_end':
                activeThinking = false;
                results.push(event);
                if (animationsEnabled && !activeReasoning) showThinking('waiting');
                break;
            case 'rate_limit':
                results.push(event);
                if (animationsEnabled) showThinking('rate_limit');
                break;
            case 'operation_complete':
                activeThinking = false;
                results.push(event);
                break;
            default:
                results.push(event);
                break;
        }
    }

    return results;
}

describe('Terminal stream spinner placement', () => {
    it('adds startup spinner after operation_init', () => {
        const processed = processEventsForSpinners([{type: 'operation_init'}]);

        expect(processed).toContainEqual({type: 'thinking', context: 'startup'});
    });

    it('adds tool preparation spinner after progress updates', () => {
        const processed = processEventsForSpinners([
            {type: 'operation_init'},
            {type: 'reasoning', content: 'Planning attack...'},
            {type: 'progress_update', step: 1},
        ]);

        const progressIndex = processed.findIndex(event => event.type === 'progress_update');
        expect(processed[progressIndex + 1]).toEqual({type: 'thinking', context: 'tool_preparation'});
    });

    it('shows tool execution and waiting spinners around tool calls', () => {
        const processed = processEventsForSpinners([
            {type: 'progress_update', step: 1},
            {type: 'tool_start', tool_name: 'http_request'},
            {type: 'output', content: 'Response data'},
            {type: 'tool_end', tool_name: 'http_request'},
        ]);

        expect(processed).toContainEqual({type: 'thinking', context: 'tool_execution'});
        expect(processed).toContainEqual({type: 'thinking', context: 'waiting'});
    });

    it('does not add thinking events when animations are disabled', () => {
        const processed = processEventsForSpinners([
            {type: 'operation_init'},
            {type: 'progress_update', step: 1},
            {type: 'tool_start', tool_name: 'shell'},
            {type: 'rate_limit'},
        ], false);

        expect(processed.filter(event => event.type === 'thinking')).toHaveLength(0);
    });
});
