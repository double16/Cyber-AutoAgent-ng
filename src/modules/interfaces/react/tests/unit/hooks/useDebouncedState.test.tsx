import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {jest} from '@jest/globals';
import {useDebouncedState} from '../../../src/hooks/useDebouncedState.js';

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

describe('useDebouncedState', () => {
    beforeEach(() => {
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('delays state updates until the timeout fires', () => {
        const hook = renderHook(() => useDebouncedState('initial', 50));

        act(() => {
            hook.current[1]('next');
        });
        expect(hook.current[0]).toBe('initial');

        act(() => {
            jest.advanceTimersByTime(50);
        });
        expect(hook.current[0]).toBe('next');

        hook.unmount();
    });

    it('replaces pending updates and flushes immediately', () => {
        const hook = renderHook(() => useDebouncedState(0, 1000));

        act(() => {
            hook.current[1](1);
            hook.current[1](2);
            hook.current[2]();
        });

        expect(hook.current[0]).toBe(2);
        act(() => {
            jest.advanceTimersByTime(1000);
        });
        expect(hook.current[0]).toBe(2);

        hook.unmount();
    });

    it('ignores flush without pending value and clears timeout on unmount', () => {
        const clearSpy = jest.spyOn(globalThis, 'clearTimeout');
        const hook = renderHook(() => useDebouncedState<string | null>(null, 1000));

        act(() => {
            hook.current[2]();
            hook.current[1]('pending');
        });
        hook.unmount();

        expect(clearSpy).toHaveBeenCalled();
    });
});
