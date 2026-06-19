import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {describe, expect, it} from '@jest/globals';
import {
    ConfigEditorLazy,
    DocumentationViewerLazy,
    ModuleSelectorLazy,
    SwarmDisplayLazy,
    TerminalLazy,
} from '../../../src/components/LazyComponents.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

describe('LazyComponents', () => {
    it('renders descriptive suspense fallbacks for lazy component wrappers', () => {
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <>
                    <ConfigEditorLazy/>
                    <DocumentationViewerLazy/>
                    <ModuleSelectorLazy/>
                    <SwarmDisplayLazy/>
                    <TerminalLazy/>
                </>
            );
        });

        const text = textFromTree(view.toJSON());
        expect(text).toContain('Loading Configuration Editor...');
        expect(text).toContain('Loading Documentation...');
        expect(text).toContain('Loading Module Selector...');
        expect(text).toContain('Loading Swarm Display...');
        expect(text).toContain('Loading Terminal...');
    });
});
