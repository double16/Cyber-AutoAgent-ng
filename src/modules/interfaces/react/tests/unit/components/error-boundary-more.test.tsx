import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {beforeEach, describe, expect, it, jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const loggingService = {
    error: jest.fn(),
    warn: jest.fn(),
};

jest.unstable_mockModule('../../../src/services/LoggingService.js', () => ({
    loggingService,
}));

jest.unstable_mockModule('../../../src/components/Header.js', () => ({
    Header: () => <header>header</header>,
}));

const load = async () => import('../../../src/components/ErrorBoundary.js');

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

const Boom = ({message}: {message: string}) => {
    throw new Error(message);
};

describe('ErrorBoundary additional coverage', () => {
    beforeEach(() => {
        loggingService.error.mockClear();
        loggingService.warn.mockClear();
    });

    it('renders custom fallback and invokes onError', async () => {
        const {ErrorBoundary} = await load();
        const onError = jest.fn();
        let view!: TestRenderer.ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(
                <ErrorBoundary fallback={<span>fallback</span>} onError={onError}>
                    <Boom message="regular failure"/>
                </ErrorBoundary>
            );
        });

        expect(textFromTree(view.toJSON())).toBe('fallback');
        expect(loggingService.error).toHaveBeenCalledWith(
            'ErrorBoundary caught an error:',
            expect.any(Error),
            expect.any(Object)
        );
        expect(onError).toHaveBeenCalledWith(expect.any(Error), expect.any(Object));
    });

    it('renders memory-exhaustion recovery copy', async () => {
        const {ErrorBoundary} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(
                <ErrorBoundary>
                    <Boom message="memory access out of bounds"/>
                </ErrorBoundary>
            );
        });

        const text = textFromTree(view.toJSON());
        expect(text).toContain('Memory Exhaustion Error');
        expect(text).toContain('RESTART REQUIRED');
        expect(loggingService.warn).toHaveBeenCalledWith('WASM memory exhaustion detected - application restart recommended');

        expect(text).toContain('Memory Exhaustion Error');
        expect(text).toContain('RESTART REQUIRED');
        expect(loggingService.warn).toHaveBeenCalledWith('WASM memory exhaustion detected - application restart recommended');
    });

    it('can reset error state through the retry handler', async () => {
        const {ErrorBoundary} = await load();
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <ErrorBoundary>
                    <span>ok</span>
                </ErrorBoundary>
            );
        });

        const instance = view.root.findByType(ErrorBoundary).instance as any;
        act(() => {
            instance.setState({hasError: true, error: new Error('temporary'), errorInfo: null});
        });
        expect(instance.state.hasError).toBe(true);

        act(() => {
            instance.handleRetry();
        });
        expect(instance.state.hasError).toBe(false);
    });

    it('renders normal error details, development stack, and restart exits', async () => {
        const {ErrorBoundary} = await load();
        const originalEnv = process.env.NODE_ENV;
        const exit = jest.spyOn(process, 'exit').mockImplementation((() => undefined) as never);
        Object.defineProperty(process.env, 'NODE_ENV', {value: 'development', configurable: true});
        let view!: TestRenderer.ReactTestRenderer;

        act(() => {
            view = TestRenderer.create(
                <ErrorBoundary>
                    <span>ok</span>
                </ErrorBoundary>
            );
        });

        const instance = view.root.findByType(ErrorBoundary).instance as any;
        const error = new Error('ordinary failure');
        error.stack = 'stack line';
        act(() => {
            instance.setState({hasError: true, error, errorInfo: null});
        });

        const text = textFromTree(view.toJSON());
        expect(text).toContain('Application Error');
        expect(text).toContain('ordinary failure');
        expect(text).toContain('Stack trace:');
        expect(text).toContain('Press R to retry');

        act(() => {
            instance.handleRestart();
        });
        expect(exit).toHaveBeenCalledWith(1);

        exit.mockRestore();
        Object.defineProperty(process.env, 'NODE_ENV', {value: originalEnv, configurable: true});
    });
});
