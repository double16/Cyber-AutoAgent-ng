import {describe, expect, it} from '@jest/globals';

import {formatDuration, sanitizeForLogging} from '../../../src/utils/logger.js';

describe('formatDuration', () => {
    it('formats sub-minute durations as pluralized seconds', () => {
        expect(formatDuration(0)).toBe('0 seconds');
        expect(formatDuration(1000)).toBe('1 second');
        expect(formatDuration(59000)).toBe('59 seconds');
    });

    it('rounds to the nearest second and clamps negative durations to zero', () => {
        expect(formatDuration(1499)).toBe('1 second');
        expect(formatDuration(1500)).toBe('2 seconds');
        expect(formatDuration(-1000)).toBe('0 seconds');
    });

    it('formats durations of at least one minute as HH:MM:SS', () => {
        expect(formatDuration(60000)).toBe('00:01:00');
        expect(formatDuration(61000)).toBe('00:01:01');
        expect(formatDuration(3599000)).toBe('00:59:59');
        expect(formatDuration(3600000)).toBe('01:00:00');
        expect(formatDuration(3661000)).toBe('01:01:01');
    });

    it('pads multi-part durations and supports durations longer than one day', () => {
        expect(formatDuration(10 * 60 * 60 * 1000 + 5 * 60 * 1000 + 3 * 1000)).toBe('10:05:03');
        expect(formatDuration(25 * 60 * 60 * 1000)).toBe('25:00:00');
    });
});

describe('sanitizeForLogging', () => {
    it('returns unchanged plain text', () => {
        expect(sanitizeForLogging('plain log line')).toBe('plain log line');
    });

    it('removes CSI formatting and cursor control sequences', () => {
        const input = '\x1b[31mred\x1b[0m normal \x1b[?25lhidden cursor\x1b[2K';

        expect(sanitizeForLogging(input)).toBe('red normal hidden cursor');
    });

    it('removes OSC sequences terminated by bell or end of string', () => {
        expect(sanitizeForLogging('before \x1b]0;window title\x07 after')).toBe('before  after');
        expect(sanitizeForLogging('before \x1b]2;unterminated title')).toBe('before ');
    });

    it('removes device control and text area commands', () => {
        const input = 'safe \x1bP1;2payload\x1b\\ text \x1b^privacy command end';

        expect(sanitizeForLogging(input)).toBe('safe  text ');
    });

    it('removes disallowed control characters and replacement characters', () => {
        const input = 'a\x00b\x08c\x0bd\x1ce\x1ef\x1fg\x7fh\uFFFD';

        expect(sanitizeForLogging(input)).toBe('abcdefgh');
    });

    it('preserves tabs and newlines', () => {
        expect(sanitizeForLogging('col1\tcol2\nnext line')).toBe('col1\tcol2\nnext line');
    });
});
