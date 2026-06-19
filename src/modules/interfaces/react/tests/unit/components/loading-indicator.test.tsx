import React from 'react';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from 'react-test-renderer';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

jest.unstable_mockModule('ink-spinner', () => ({
    default: ({type}: { type?: string }) => <span>spinner:{type}</span>,
}));

const load = async () => import('../../../src/components/LoadingIndicator.js');

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

describe('LoadingIndicator', () => {
    beforeEach(() => {
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('renders custom text when phases are disabled and animates dots', async () => {
        const {LoadingIndicator} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(
                <LoadingIndicator text="Working" showPhases={false} spinnerType="line" color="green"/>
            );
        });

        expect(textFromTree(view.toJSON())).toContain('spinner:line');
        expect(textFromTree(view.toJSON())).toContain('Working');

        act(() => {
            jest.advanceTimersByTime(500);
        });
        expect(textFromTree(view.toJSON())).toContain('Working.');
    });

    it('cycles through phase messages and wraps dot animation', async () => {
        const {LoadingIndicator} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(<LoadingIndicator/>);
        });
        expect(textFromTree(view.toJSON())).toContain('Analyzing security posture');

        act(() => {
            jest.advanceTimersByTime(3000);
        });
        expect(textFromTree(view.toJSON())).toContain('Scanning network services');

        act(() => {
            jest.advanceTimersByTime(2000);
        });
        expect(textFromTree(view.toJSON())).not.toContain('....');
    });
});
