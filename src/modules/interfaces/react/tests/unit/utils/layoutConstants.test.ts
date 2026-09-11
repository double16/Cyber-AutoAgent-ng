import {
  calculateAvailableHeight,
  calculateAvailableWidth,
  calculateToolContentHeight,
  CONTENT_AREA,
  RESERVED_VERTICAL_SPACE,
  TOOL_LAYOUT,
} from '../../../src/utils/layoutConstants.js';
import {describe, expect, it} from '@jest/globals';

describe('layoutConstants', () => {
    it('computes reserved vertical and tool layout totals', () => {
        expect(RESERVED_VERTICAL_SPACE.TOTAL).toBe(
            RESERVED_VERTICAL_SPACE.HEADER +
            RESERVED_VERTICAL_SPACE.FOOTER +
            RESERVED_VERTICAL_SPACE.STATUS_BAR +
            RESERVED_VERTICAL_SPACE.PADDING
        );
        expect(TOOL_LAYOUT.RESERVED).toBe(TOOL_LAYOUT.HEADER + TOOL_LAYOUT.PADDING * 2);
    });

    it('calculates available height with minimum fallback', () => {
        expect(calculateAvailableHeight(40)).toBe(40 - RESERVED_VERTICAL_SPACE.TOTAL);
        expect(calculateAvailableHeight(5)).toBe(CONTENT_AREA.MIN_HEIGHT);
    });

    it('calculates tool content height with minimum fallback', () => {
        expect(calculateToolContentHeight(40)).toBe(
            calculateAvailableHeight(40) - TOOL_LAYOUT.RESERVED
        );
        expect(calculateToolContentHeight(5)).toBe(
            calculateAvailableHeight(5) - TOOL_LAYOUT.RESERVED
        );
    });

    it('calculates available width with minimum fallback', () => {
        expect(calculateAvailableWidth(100)).toBe(89);
        expect(calculateAvailableWidth(10)).toBe(CONTENT_AREA.MIN_WIDTH);
    });
});
