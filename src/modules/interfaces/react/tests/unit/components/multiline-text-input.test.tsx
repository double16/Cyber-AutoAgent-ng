import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let lastTextInputProps: any;

jest.unstable_mockModule('ink-text-input', () => ({
    default: (props: any) => {
        lastTextInputProps = props;
        return <input data-value={props.value} placeholder={props.placeholder}/>;
    },
}));

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
        lastTextInputProps = undefined;
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('renders previous lines and debounces changes to the parent value', async () => {
        const {MultiLineTextInput} = await load();
        const onChange = jest.fn();
        const onSubmit = jest.fn();

        let view!: TestRenderer.ReactTestRenderer;
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
        expect(lastTextInputProps.value).toBe('second');

        act(() => {
            lastTextInputProps.onChange('updated');
        });
        expect(onChange).not.toHaveBeenCalled();

        act(() => {
            jest.advanceTimersByTime(100);
        });
        expect(onChange).toHaveBeenCalledWith('first\nupdated');

        act(() => {
            lastTextInputProps.onSubmit('submitted');
        });
        expect(onSubmit).toHaveBeenCalledWith('first\nsubmitted');
    });

    it('applies external value changes while idle', async () => {
        const {MultiLineTextInput} = await load();
        const onChange = jest.fn();
        let view!: TestRenderer.ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(<MultiLineTextInput value="one" onChange={onChange}/>);
        });
        expect(lastTextInputProps.value).toBe('one');

        act(() => {
            view.update(<MultiLineTextInput value={'alpha\nbeta'} onChange={onChange}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('alpha');
        expect(lastTextInputProps.value).toBe('beta');
    });

    it('handles single-line edits, pending external changes, and submits without a handler', async () => {
        const {MultiLineTextInput} = await load();
        const onChange = jest.fn();
        let view!: TestRenderer.ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(<MultiLineTextInput value="" onChange={onChange}/>);
        });
        expect(lastTextInputProps.value).toBe('');

        act(() => {
            lastTextInputProps.onChange('typed');
            view.update(<MultiLineTextInput value="external" onChange={onChange}/>);
        });
        expect(lastTextInputProps.value).toBe('typed');
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
                lastTextInputProps.onSubmit('ignored');
            });
        }).not.toThrow();
    });
});
