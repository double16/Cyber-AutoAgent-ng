import {describe, expect, it, jest} from '@jest/globals';

jest.unstable_mockModule('child_process', () => ({
    exec: jest.fn(),
    spawn: jest.fn(),
    execFileSync: jest.fn(),
}));

const load = async () => import('../../../src/services/PythonExecutionService.js');

describe('PythonExecutionService cleanup', () => {
    it('clears pending startup timers during cleanup', async () => {
        jest.useFakeTimers();
        try {
            const {PythonExecutionService} = await load();
            const service = new PythonExecutionService();
            const callback = jest.fn();

            (service as any).scheduleStartupTimer(callback, 1000);
            expect(jest.getTimerCount()).toBe(1);

            service.cleanup();
            expect(jest.getTimerCount()).toBe(0);

            jest.advanceTimersByTime(1000);
            expect(callback).not.toHaveBeenCalled();
        } finally {
            jest.useRealTimers();
        }
    });
});
