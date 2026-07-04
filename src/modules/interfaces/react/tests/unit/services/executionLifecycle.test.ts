import {EventEmitter} from 'events';
import {describe, expect, it, jest} from '@jest/globals';
import {stopExecution} from '../../../src/services/executionLifecycle.js';

describe('executionLifecycle', () => {
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
});

