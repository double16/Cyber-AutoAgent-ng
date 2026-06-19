import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {jest} from '@jest/globals';
import {
  useAnimationFrameBatcher,
  useBatchedReducer,
  useBatchedState,
  useEventBatcher,
} from '../../../src/utils/useBatchedState.js';

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
        unmount() {
            act(() => {
                renderer.unmount();
            });
        },
    };
}

describe('useBatchedState utilities', () => {
    beforeEach(() => {
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.useRealTimers();
        jest.restoreAllMocks();
    });

    it('batches state updates until the timer flushes', () => {
        const hook = renderHook(() => useBatchedState(0, 50));

        act(() => {
            hook.current[1](value => value + 1);
            hook.current[1](value => value + 2);
        });
        expect(hook.current[0]).toBe(0);

        act(() => {
            jest.advanceTimersByTime(50);
        });
        expect(hook.current[0]).toBe(3);

        hook.unmount();
    });

    it('flushes immediately when max batch size is reached', () => {
        const hook = renderHook(() => useBatchedState('a', 1000, 2));

        act(() => {
            hook.current[1](value => `${value}b`);
            hook.current[1](value => `${value}c`);
        });

        expect(hook.current[0]).toBe('abc');
        hook.unmount();
    });

    it('supports explicit flush and value replacement updates', () => {
        const hook = renderHook(() => useBatchedState({count: 0}, 1000));

        act(() => {
            hook.current[1]({count: 5});
            hook.current[2]();
        });

        expect(hook.current[0]).toEqual({count: 5});
        hook.unmount();
    });

    it('batches reducer actions through useBatchedReducer', () => {
        const reducer = (state: number, action: { by: number }) => state + action.by;
        const hook = renderHook(() => useBatchedReducer(reducer, 10, 50));

        act(() => {
            hook.current[1]({by: 2});
            hook.current[1]({by: 3});
            jest.advanceTimersByTime(50);
        });

        expect(hook.current[0]).toBe(15);
        hook.unmount();
    });

    it('batches event streams by size and timer', () => {
        const onBatch = jest.fn();
        const hook = renderHook(() => useEventBatcher<string>(onBatch, 50, 2));

        act(() => {
            hook.current.addEvent('a');
            hook.current.addEvent('b');
        });
        expect(onBatch).toHaveBeenCalledWith(['a', 'b']);

        act(() => {
            hook.current.addEvent('c');
            jest.advanceTimersByTime(50);
        });
        expect(onBatch).toHaveBeenLastCalledWith(['c']);

        hook.unmount();
    });

    it('flushes pending events during unmount cleanup', () => {
        const onBatch = jest.fn();
        const hook = renderHook(() => useEventBatcher<string>(onBatch, 1000));

        act(() => {
            hook.current.addEvent('pending');
        });
        hook.unmount();

        expect(onBatch).toHaveBeenCalledWith(['pending']);
    });

    it('batches animation frame items by size and frame callback', () => {
        const onBatch = jest.fn();
        let rafCallback: FrameRequestCallback | undefined;
        jest.spyOn(globalThis, 'requestAnimationFrame').mockImplementation(callback => {
            rafCallback = callback;
            return 123;
        });
        jest.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => {
        });

        const hook = renderHook(() => useAnimationFrameBatcher<string>(onBatch, 2));

        act(() => {
            hook.current.addItem('a');
            hook.current.addItem('b');
        });
        expect(onBatch).toHaveBeenCalledWith(['a', 'b']);

        act(() => {
            hook.current.addItem('c');
            rafCallback?.(performance.now());
        });
        expect(onBatch).toHaveBeenLastCalledWith(['c']);

        hook.unmount();
    });
});
