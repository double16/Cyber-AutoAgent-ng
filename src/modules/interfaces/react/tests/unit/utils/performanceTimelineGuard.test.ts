import {afterEach, describe, expect, it, jest} from '@jest/globals';

import {installPerformanceTimelineGuard} from '../../../src/utils/performanceTimelineGuard.js';

describe('installPerformanceTimelineGuard', () => {
    afterEach(() => {
        delete process.env.CYBER_KEEP_PERFORMANCE_MEASURES;
    });

    it('disables performance measurements by default', () => {
        const measure = jest.fn(() => ({name: 'render'}));
        const clearMarks = jest.fn();
        const clearMeasures = jest.fn();
        const perf = {
            measure,
            clearMarks,
            clearMeasures,
        };

        expect(installPerformanceTimelineGuard(perf)).toBe(true);

        const result = perf.measure('render', {start: 1, end: 2});

        expect(result).toBeUndefined();
        expect(measure).not.toHaveBeenCalled();
        expect(clearMeasures).toHaveBeenCalledWith();
        expect(clearMarks).toHaveBeenCalledWith();
    });

    it('leaves performance measurements enabled when profiling is explicitly requested', () => {
        process.env.CYBER_KEEP_PERFORMANCE_MEASURES = 'true';
        const perf = {
            measure: jest.fn(() => 'ok'),
            clearMeasures: jest.fn(),
        };

        expect(installPerformanceTimelineGuard(perf)).toBe(false);
        expect(perf.measure('render')).toBe('ok');
        expect(perf.clearMeasures).not.toHaveBeenCalled();
    });

    it('only installs once for a target performance object', () => {
        const perf = {
            measure: () => 'ok',
            clearMeasures: () => {},
        };

        expect(installPerformanceTimelineGuard(perf)).toBe(true);
        expect(installPerformanceTimelineGuard(perf)).toBe(false);
        expect(perf.measure()).toBeUndefined();
    });
});
