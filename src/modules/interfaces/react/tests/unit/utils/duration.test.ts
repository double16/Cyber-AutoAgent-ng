import {describe, expect, it} from '@jest/globals';
import {estimateEtaSeconds, parseDurationSeconds} from '../../../src/utils/duration.js';

describe('duration utilities', () => {
    it.each([
        ['10s', 10],
        ['1m', 60],
        ['1h', 3600],
        ['5m 20s', 320],
        ['1h 5m 20s', 3920],
        ['1h5m20s', 3920],
    ])('parses %s to seconds', (duration, expectedSeconds) => {
        expect(parseDurationSeconds(duration)).toBe(expectedSeconds);
    });

    it.each([
        '',
        'unknown',
        '10 seconds',
        '10s elapsed',
        '1d',
        '1m - 2s',
    ])('rejects invalid duration %s', (duration) => {
        expect(parseDurationSeconds(duration)).toBeNull();
    });

    it('estimates ETA from elapsed duration and budget progress', () => {
        expect(estimateEtaSeconds('5m 10s', 25)).toBe(1240);
        expect(estimateEtaSeconds('1h 5m 20s', 50)).toBe(7840);
    });

    it.each([
        [undefined, 50],
        ['0s', 50],
        ['unknown', 50],
        ['10s', undefined],
        ['10s', 0],
        ['10s', 100],
        ['10s', Number.NaN],
    ])('omits ETA for invalid inputs duration=%s progress=%s', (duration, progressPercent) => {
        expect(estimateEtaSeconds(duration, progressPercent)).toBeNull();
    });
});
