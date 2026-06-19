import {RingBuffer} from '../../../src/utils/RingBuffer.js';
import {describe, expect, it} from '@jest/globals';

describe('RingBuffer', () => {
    it('rejects invalid capacities', () => {
        expect(() => new RingBuffer(0)).toThrow('RingBuffer capacity must be > 0');
        expect(() => new RingBuffer(Number.POSITIVE_INFINITY)).toThrow('RingBuffer capacity must be > 0');
    });

    it('stores pushed items in insertion order until capacity', () => {
        const buffer = new RingBuffer<number>(3);

        buffer.push(1);
        buffer.push(2);

        expect(buffer.toArray()).toEqual([1, 2]);
    });

    it('overwrites oldest items after capacity is reached', () => {
        const buffer = new RingBuffer<number>(3);

        buffer.pushMany([1, 2, 3, 4, 5]);

        expect(buffer.toArray()).toEqual([3, 4, 5]);
    });

    it('clears existing items and accepts new data afterward', () => {
        const buffer = new RingBuffer<string>(2);

        buffer.pushMany(['a', 'b', 'c']);
        buffer.clear();
        expect(buffer.toArray()).toEqual([]);

        buffer.push('d');
        expect(buffer.toArray()).toEqual(['d']);
    });
});
