import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {useEventGroups, useEventStream, useSwarmTracking} from '../../../src/hooks/useEventStream.js';
import {EVENT_TYPES} from '../../../src/constants/config.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

function renderHook<T>(hook: () => T) {
    let current: T;
    const Harness = () => {
        current = hook();
        return null;
    };

    let renderer: TestRenderer.ReactTestRenderer;
    act(() => {
        renderer = TestRenderer.create(<Harness/>);
    });

    return {
        get current() {
            return current!;
        },
        rerender() {
            act(() => {
                renderer.update(<Harness/>);
            });
        },
        unmount() {
            act(() => {
                renderer.unmount();
            });
        },
    };
}

describe('useEventStream hooks', () => {
    it('adds, processes, and clears display events', () => {
        const hook = renderHook(() => useEventStream(25, 10));

        act(() => {
            hook.current[1].addEvent({type: EVENT_TYPES.TOOL_START, tool_name: 'shell'} as any);
        });
        expect(hook.current[0].events).toHaveLength(1);

        act(() => {
            hook.current[1].processEvent({type: EVENT_TYPES.STEP_HEADER, step: 3, maxSteps: 12} as any);
            hook.current[1].processEvent({type: EVENT_TYPES.THINKING} as any);
            hook.current[1].processEvent({type: EVENT_TYPES.TOOL_START, tool_name: 'http_request'} as any);
            hook.current[1].processEvent({type: EVENT_TYPES.REASONING, content: 'first '} as any);
            hook.current[1].processEvent({type: EVENT_TYPES.REASONING, content: 'second'} as any);
            hook.current[1].processEvent({type: EVENT_TYPES.THINKING_END} as any);
        });

        expect(hook.current[0]).toEqual(expect.objectContaining({
            currentStep: 3,
            maxSteps: 12,
            isThinking: false,
            lastToolName: 'http_request',
            reasoningBuffer: ['first ', 'second'],
        }));

        act(() => {
            hook.current[1].flushReasoningBuffer();
        });
        expect(hook.current[0].reasoningBuffer).toEqual([]);
        expect(hook.current[0].events.at(-1)).toEqual(expect.objectContaining({
            type: EVENT_TYPES.REASONING,
            content: 'first second',
        }));

        act(() => {
            hook.current[1].clearEvents();
        });
        expect(hook.current[0]).toEqual(expect.objectContaining({
            events: [],
            currentStep: 0,
            reasoningBuffer: [],
            lastToolName: null,
        }));

        hook.unmount();
    });

    it('does not flush empty reasoning buffers', () => {
        const hook = renderHook(() => useEventStream());
        const stateBefore = hook.current[0];

        act(() => {
            hook.current[1].flushReasoningBuffer();
        });

        expect(hook.current[0]).toBe(stateBefore);
        hook.unmount();
    });

    it('groups consecutive reasoning events separately from single events', () => {
        const events = [
            {type: EVENT_TYPES.REASONING, content: 'a'},
            {type: EVENT_TYPES.REASONING, content: 'b'},
            {type: EVENT_TYPES.TOOL_START, tool_name: 'shell'},
            {type: EVENT_TYPES.REASONING, content: 'c'},
        ] as any[];

        const hook = renderHook(() => useEventGroups(events));

        expect(hook.current).toEqual([
            {type: 'reasoning_group', events: events.slice(0, 2), startIdx: 0},
            {type: 'single', events: [events[2]], startIdx: 2},
            {type: 'reasoning_group', events: [events[3]], startIdx: 3},
        ]);
        hook.unmount();
    });

    it('tracks swarm operations and ignores handoffs without an active swarm', () => {
        const hook = renderHook(() => useSwarmTracking());

        act(() => {
            hook.current.handoffAgent('none', 'ignored');
        });
        expect(hook.current.getActiveSwarm()).toBeNull();

        act(() => {
            hook.current.startSwarm('swarm-1', ['planner', 'tester']);
        });
        expect(hook.current.activeSwarmId).toBe('swarm-1');
        expect(hook.current.getActiveSwarm()).toEqual(expect.objectContaining({
            id: 'swarm-1',
            currentAgent: 'planner',
            handoffCount: 0,
            status: 'running',
        }));

        act(() => {
            hook.current.handoffAgent('planner', 'tester');
        });
        expect(hook.current.getActiveSwarm()).toEqual(expect.objectContaining({
            currentAgent: 'tester',
            handoffCount: 1,
        }));

        act(() => {
            hook.current.completeSwarm('swarm-1', 'failed');
        });
        expect(hook.current.activeSwarmId).toBeNull();
        expect(hook.current.swarmOperations.get('swarm-1')).toEqual(expect.objectContaining({
            status: 'failed',
            endTime: expect.any(Number),
        }));

        hook.unmount();
    });
});
