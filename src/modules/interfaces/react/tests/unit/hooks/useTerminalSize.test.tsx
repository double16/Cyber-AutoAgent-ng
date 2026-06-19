import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {afterEach, beforeEach, describe, expect, it} from '@jest/globals';
import {EventEmitter} from 'events';
import {useTerminalSize} from '../../../src/hooks/useTerminalSize.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('useTerminalSize', () => {
    const originalStdout = process.stdout;

    beforeEach(() => {
        const stdout = new EventEmitter() as any;
        stdout.columns = 100;
        stdout.rows = 40;
        Object.defineProperty(process, 'stdout', {
            value: stdout,
            configurable: true,
        });
    });

    afterEach(() => {
        Object.defineProperty(process, 'stdout', {
            value: originalStdout,
            configurable: true,
        });
    });

    it('calculates padded dimensions and responds to resize events', () => {
        let current: any;
        const Harness = () => {
            current = useTerminalSize();
            return null;
        };

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<Harness/>);
        });

        expect(current).toEqual({columns: 100, rows: 40, availableWidth: 92, availableHeight: 36});

        act(() => {
            (process.stdout as any).columns = 30;
            (process.stdout as any).rows = 10;
            process.stdout.emit('resize');
        });
        expect(current).toEqual({columns: 30, rows: 10, availableWidth: 60, availableHeight: 20});

        act(() => {
            view.unmount();
            (process.stdout as any).columns = 120;
            process.stdout.emit('resize');
        });
        expect(current.columns).toBe(30);
    });

    it('uses default terminal dimensions when stdout dimensions are missing', () => {
        delete (process.stdout as any).columns;
        delete (process.stdout as any).rows;
        let current: any;
        const Harness = () => {
            current = useTerminalSize();
            return null;
        };

        act(() => {
            TestRenderer.create(<Harness/>);
        });

        expect(current).toEqual({columns: 80, rows: 24, availableWidth: 72, availableHeight: 20});
    });
});
