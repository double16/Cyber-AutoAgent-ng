import React from 'react';
import TestRenderer, {ReactTestRenderer, act} from '../test-renderer.js';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const load = async () => {
    const {MultiLineTextInput} = await import('../../../src/components/MultiLineTextInput.js');
    return {MultiLineTextInput};
};

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

describe('MultiLineTextInput', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        delete (global as any).__inkInputHandler;
    });

    afterEach(() => {
        jest.useRealTimers();
        delete (global as any).__inkInputHandler;
    });

    const sendInput = (input = '', key: Record<string, boolean> = {}) => {
        act(() => {
            (global as any).__inkInputHandler?.(input, key);
        });
    };

    it('renders previous lines and debounces changes to the parent value', async () => {
        const {MultiLineTextInput} = await load();
        const onChange = jest.fn();
        const onSubmit = jest.fn();

        let view!: ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <MultiLineTextInput
                    value={'first\nsecond'}
                    onChange={onChange}
                    onSubmit={onSubmit}
                    placeholder="type here"
                    focus
                    showCursor
                    textColor="cyan"
                />
            );
        });

        expect(textFromTree(view.toJSON())).toContain('first');
        expect(textFromTree(view.toJSON())).toContain('second');

        sendInput('a', {ctrl: true});
        sendInput('updated');
        expect(onChange).not.toHaveBeenCalled();

        act(() => {
            jest.advanceTimersByTime(100);
        });
        expect(onChange).toHaveBeenCalledWith('first\nupdatedsecond');

        act(() => {
            sendInput('', {return: true});
        });
        expect(onSubmit).toHaveBeenCalledWith('first\nupdatedsecond');
    });

    it('applies external value changes while idle', async () => {
        const {MultiLineTextInput} = await load();
        const onChange = jest.fn();
        let view!: ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(<MultiLineTextInput value="one" onChange={onChange}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('one');

        act(() => {
            view.update(<MultiLineTextInput value={'alpha\nbeta'} onChange={onChange}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('alpha');
        expect(textFromTree(view.toJSON())).toContain('beta');
    });

    it('handles single-line edits, pending external changes, and submits without a handler', async () => {
        const {MultiLineTextInput} = await load();
        const onChange = jest.fn();
        let view!: ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(<MultiLineTextInput value="" onChange={onChange}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('█');

        act(() => {
            sendInput('typed');
            view.update(<MultiLineTextInput value="external" onChange={onChange}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('typed');
        expect(onChange).not.toHaveBeenCalled();

        act(() => {
            jest.advanceTimersByTime(100);
        });
        expect(onChange).toHaveBeenCalledWith('typed');

        act(() => {
            view.update(<MultiLineTextInput value="typed" onChange={onChange}/>);
        });

        expect(() => {
            act(() => {
                sendInput('', {return: true});
            });
        }).not.toThrow();
    });

    it('supports readline-style editing on the active command line', async () => {
        const {MultiLineTextInput} = await load();
        const onChange = jest.fn();
        let view!: ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(<MultiLineTextInput value="run scan" onChange={onChange}/>);
        });

        sendInput('a', {ctrl: true});
        sendInput('quick ');
        act(() => {
            jest.advanceTimersByTime(100);
        });
        expect(onChange).toHaveBeenLastCalledWith('quick run scan');

        act(() => {
            view.update(<MultiLineTextInput value="quick run scan" onChange={onChange}/>);
        });
        sendInput('e', {ctrl: true});
        sendInput(' now');
        act(() => {
            jest.advanceTimersByTime(100);
        });
        expect(onChange).toHaveBeenLastCalledWith('quick run scan now');

        act(() => {
            view.update(<MultiLineTextInput value="quick run scan now" onChange={onChange}/>);
        });
        sendInput('b', {ctrl: true});
        sendInput('k', {ctrl: true});
        act(() => {
            jest.advanceTimersByTime(100);
        });
        expect(onChange).toHaveBeenLastCalledWith('quick run scan no');

        expect(textFromTree(view.toJSON())).toContain('quick run scan');
    });
});
