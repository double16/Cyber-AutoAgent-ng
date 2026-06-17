import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {describe, expect, it, jest} from '@jest/globals';
import {useTextBuffer} from '../../../src/hooks/useTextBuffer.js';

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

describe('useTextBuffer', () => {
    it('initializes from the provided value and inserts at the cursor', () => {
        const onChange = jest.fn();
        const hook = renderHook(() => useTextBuffer({initialValue: 'hello', onChange}));

        expect(hook.current.text).toBe('hello');
        expect(hook.current.cursorPosition).toBe(5);

        act(() => {
            hook.current.moveLeft();
            hook.current.moveLeft();
        });
        expect(hook.current.cursorPosition).toBe(3);

        act(() => {
            hook.current.insert('p');
        });
        expect(hook.current.text).toBe('helplo');
        expect(hook.current.cursorPosition).toBe(4);
        expect(onChange).toHaveBeenLastCalledWith('helplo');

        hook.unmount();
    });

    it('deletes before and after the cursor with boundary guards', () => {
        const onChange = jest.fn();
        const hook = renderHook(() => useTextBuffer({initialValue: 'abcd', onChange}));

        act(() => {
            hook.current.moveToStart();
            hook.current.deleteBeforeCursor();
        });
        expect(hook.current.text).toBe('abcd');

        act(() => {
            hook.current.deleteAfterCursor();
        });
        expect(hook.current.text).toBe('bcd');
        expect(hook.current.cursorPosition).toBe(0);
        expect(onChange).toHaveBeenLastCalledWith('bcd');

        act(() => {
            hook.current.moveRight();
        });
        act(() => {
            hook.current.deleteBeforeCursor();
        });
        expect(hook.current.text).toBe('cd');
        expect(hook.current.cursorPosition).toBe(0);
        expect(onChange).toHaveBeenLastCalledWith('cd');

        act(() => {
            hook.current.moveToEnd();
            hook.current.deleteAfterCursor();
        });
        expect(hook.current.text).toBe('cd');

        hook.unmount();
    });

    it('moves within bounds, sets text, and clears', () => {
        const onChange = jest.fn();
        const hook = renderHook(() => useTextBuffer({onChange}));

        act(() => {
            hook.current.moveLeft();
            hook.current.moveRight();
        });
        expect(hook.current.cursorPosition).toBe(0);

        act(() => {
            hook.current.setText('target', 2);
        });
        expect(hook.current.text).toBe('target');
        expect(hook.current.cursorPosition).toBe(2);
        expect(onChange).toHaveBeenLastCalledWith('target');

        act(() => {
            hook.current.moveToEnd();
            hook.current.moveRight();
        });
        expect(hook.current.cursorPosition).toBe(6);

        act(() => {
            hook.current.clear();
        });
        expect(hook.current.text).toBe('');
        expect(hook.current.cursorPosition).toBe(0);
        expect(onChange).toHaveBeenLastCalledWith('');

        hook.unmount();
    });
});
