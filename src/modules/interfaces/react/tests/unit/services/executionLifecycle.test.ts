import {EventEmitter} from 'events';
import {afterEach, describe, expect, it, jest} from '@jest/globals';
import {stopExecution} from '../../../src/services/executionLifecycle.js';

describe('executionLifecycle', () => {
    afterEach(() => {
        jest.useRealTimers();
    });

    it('prefers the execution handle and can clean up the service', async () => {
        const service = new EventEmitter() as any;
        service.stop = jest.fn(async () => undefined);
        service.cleanup = jest.fn();
        const handle = {
            id: 'handle-1',
            result: Promise.resolve({success: false, durationMs: 0}),
            stop: jest.fn(async () => undefined),
            isActive: jest.fn(() => true),
        };

        await stopExecution({
            executionHandle: handle,
            executionService: service,
            cleanup: true,
            removeListeners: true,
        });

        expect(handle.stop).toHaveBeenCalledTimes(1);
        expect(service.stop).not.toHaveBeenCalled();
        expect(service.cleanup).toHaveBeenCalledTimes(1);
        expect(service.listenerCount('event')).toBe(0);
    });

    it('stops through the execution service when no handle is available', async () => {
        const service = new EventEmitter() as any;
        service.stop = jest.fn(async () => undefined);
        service.cleanup = jest.fn();

        await stopExecution({executionService: service});

        expect(service.stop).toHaveBeenCalledTimes(1);
        expect(service.cleanup).not.toHaveBeenCalled();
    });

    it('can detach and clean up a naturally completed service without stopping it again', async () => {
        const service = new EventEmitter() as any;
        service.stop = jest.fn(async () => undefined);
        service.cleanup = jest.fn();

        await stopExecution({
            executionService: service,
            skipStop: true,
            cleanup: true,
            removeListeners: true,
        });

        expect(service.stop).not.toHaveBeenCalled();
        expect(service.cleanup).toHaveBeenCalledTimes(1);
        expect(service.listenerCount('event')).toBe(0);
    });

    it('still cleans up before rethrowing stop errors', async () => {
        const service = new EventEmitter() as any;
        const error = new Error('stop failed');
        service.stop = jest.fn(async () => {
            throw error;
        });
        service.cleanup = jest.fn();

        await expect(stopExecution({
            executionService: service,
            cleanup: true,
            removeListeners: true,
        })).rejects.toThrow('stop failed');

        expect(service.cleanup).toHaveBeenCalledTimes(1);
    });

    it('times out stuck stop calls and still cleans up', async () => {
        jest.useFakeTimers();
        const service = new EventEmitter() as any;
        service.stop = jest.fn(() => new Promise<void>(() => undefined));
        service.cleanup = jest.fn();

        const stopPromise = stopExecution({
            executionService: service,
            cleanup: true,
            removeListeners: true,
            stopTimeoutMs: 25,
        });
        const expectation = expect(stopPromise).rejects.toThrow('Timed out stopping execution after 25ms');

        await jest.advanceTimersByTimeAsync(25);

        await expectation;
        expect(service.cleanup).toHaveBeenCalledTimes(1);
        expect(service.listenerCount('event')).toBe(0);
    });
});
