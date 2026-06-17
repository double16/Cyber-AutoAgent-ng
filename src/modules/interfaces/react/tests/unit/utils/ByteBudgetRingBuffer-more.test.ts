import {ByteBudgetRingBuffer} from '../../../src/utils/ByteBudgetRingBuffer.js';
import {describe, expect, it} from '@jest/globals';

describe('ByteBudgetRingBuffer additional behavior', () => {
    it('uses the default estimator for strings, primitives, and object fields', () => {
        const buffer = new ByteBudgetRingBuffer<any>(1024);

        buffer.push('hello');
        buffer.push(123);
        buffer.push({content: 'abc', command: 'id', message: 'msg', tool_name: 'shell', tool: 'tool'});

        expect(buffer.size()).toBe(3);
        expect(buffer.bytes()).toBeGreaterThan(0);
        expect(buffer.toArray()).toEqual([
            'hello',
            123,
            expect.objectContaining({content: 'abc'}),
        ]);
    });

    it('reduces over-budget items when an overflow reducer is provided', () => {
        const buffer = new ByteBudgetRingBuffer<{ content: string }>(1024, {
            estimator: item => item.content.length,
            overflowReducer: item => ({content: item.content.slice(0, 100)}),
        });

        buffer.push({content: 'x'.repeat(2000)});

        expect(buffer.toArray()).toEqual([{content: 'x'.repeat(100)}]);
        expect(buffer.bytes()).toBe(100);
    });

    it('skips over-budget items when reduction fails or remains too large', () => {
        const failing = new ByteBudgetRingBuffer<{ content: string }>(1024, {
            estimator: item => item.content.length,
            overflowReducer: () => {
                throw new Error('cannot reduce');
            },
        });
        failing.push({content: 'x'.repeat(2000)});
        expect(failing.toArray()).toEqual([]);

        const stillTooLarge = new ByteBudgetRingBuffer<{ content: string }>(1024, {
            estimator: item => item.content.length,
            overflowReducer: () => ({content: 'x'.repeat(1500)}),
        });
        stillTooLarge.push({content: 'x'.repeat(2000)});
        expect(stillTooLarge.toArray()).toEqual([]);
    });

    it('enforces budget headroom and clears state', () => {
        const buffer = new ByteBudgetRingBuffer<string>(1200, item => item.length);

        buffer.pushMany(['a'.repeat(500), 'b'.repeat(500), 'c'.repeat(500)]);

        expect(buffer.bytes()).toBeLessThanOrEqual(1080);
        expect(buffer.toArray()).toEqual(['b'.repeat(500), 'c'.repeat(500)]);

        buffer.clear();
        expect(buffer.size()).toBe(0);
        expect(buffer.bytes()).toBe(0);
        expect(buffer.toArray()).toEqual([]);
    });
});
