import {jest} from '@jest/globals';
import {CircuitBreaker, RetryManager} from '../../../src/utils/retry.js';

let consoleSpies: Array<jest.SpiedFunction<any>> = [];

beforeEach(() => {
    consoleSpies = [
        jest.spyOn(console, 'debug').mockImplementation(() => {
        }),
        jest.spyOn(console, 'info').mockImplementation(() => {
        }),
        jest.spyOn(console, 'warn').mockImplementation(() => {
        }),
        jest.spyOn(console, 'error').mockImplementation(() => {
        }),
    ];
});

afterEach(() => {
    for (const spy of consoleSpies) {
        spy.mockRestore();
    }
    consoleSpies = [];
});

describe('RetryManager', () => {
    it('retries failed operations and returns the eventual result', async () => {
        const manager = new RetryManager({
            maxRetries: 2,
            baseDelay: 0,
            maxDelay: 0,
            backoffFactor: 1,
            jitter: false,
        });
        const operation = jest.fn<() => Promise<string>>()
            .mockRejectedValueOnce(new Error('temporary failure'))
            .mockResolvedValueOnce('ok');

        await expect(manager.execute(operation, 'test retry')).resolves.toBe('ok');
        expect(operation).toHaveBeenCalledTimes(2);
    });

    it('does not retry non-retryable errors', async () => {
        const manager = new RetryManager({
            maxRetries: 3,
            baseDelay: 0,
            maxDelay: 0,
            backoffFactor: 1,
            jitter: false,
            retryCondition: error => !error.message.includes('fatal'),
        });
        const operation = jest.fn<() => Promise<string>>()
            .mockRejectedValue(new Error('fatal configuration error'));

        await expect(manager.execute(operation, 'non retryable')).rejects.toThrow('fatal configuration error');
        expect(operation).toHaveBeenCalledTimes(1);
    });

    it('throws the last error after retries are exhausted', async () => {
        const manager = new RetryManager({
            maxRetries: 2,
            baseDelay: 0,
            maxDelay: 0,
            backoffFactor: 1,
            jitter: false,
        });
        const operation = jest.fn<() => Promise<string>>()
            .mockRejectedValueOnce(new Error('first'))
            .mockRejectedValueOnce(new Error('second'))
            .mockRejectedValueOnce(new Error('final'));

        await expect(manager.execute(operation, 'exhausted')).rejects.toThrow('final');
        expect(operation).toHaveBeenCalledTimes(3);
    });
});

describe('CircuitBreaker', () => {
    it('opens after the configured failure threshold and rejects fast', async () => {
        const breaker = new CircuitBreaker({failureThreshold: 2, timeout: 1000, monitoringPeriod: 100});
        const failingOperation = jest.fn<() => Promise<string>>()
            .mockRejectedValue(new Error('service down'));

        await expect(breaker.execute(failingOperation, 'api')).rejects.toThrow('service down');
        expect(breaker.getState()).toBe('closed');
        expect(breaker.getFailureCount()).toBe(1);

        await expect(breaker.execute(failingOperation, 'api')).rejects.toThrow('service down');
        expect(breaker.getState()).toBe('open');
        expect(breaker.getFailureCount()).toBe(2);

        await expect(breaker.execute(jest.fn(), 'api')).rejects.toThrow('Circuit breaker is OPEN for api');
    });

    it('transitions to half-open after timeout and closes on success', async () => {
        const breaker = new CircuitBreaker({failureThreshold: 1, timeout: 1000, monitoringPeriod: 100});
        let now = 10_000;
        const nowSpy = jest.spyOn(Date, 'now').mockImplementation(() => now);

        try {
            await expect(breaker.execute(async () => {
                throw new Error('initial failure');
            }, 'api')).rejects.toThrow('initial failure');
            expect(breaker.getState()).toBe('open');

            now += 1_001;
            await expect(breaker.execute(async () => 'recovered', 'api')).resolves.toBe('recovered');

            expect(breaker.getState()).toBe('closed');
            expect(breaker.getFailureCount()).toBe(0);
        } finally {
            nowSpy.mockRestore();
        }
    });

    it('reopens when a half-open trial operation fails', async () => {
        const breaker = new CircuitBreaker({failureThreshold: 1, timeout: 1000, monitoringPeriod: 100});
        let now = 20_000;
        const nowSpy = jest.spyOn(Date, 'now').mockImplementation(() => now);

        try {
            await expect(breaker.execute(async () => {
                throw new Error('first failure');
            }, 'api')).rejects.toThrow('first failure');

            now += 1_001;
            await expect(breaker.execute(async () => {
                throw new Error('trial failed');
            }, 'api')).rejects.toThrow('trial failed');

            expect(breaker.getState()).toBe('open');
            await expect(breaker.execute(async () => 'blocked', 'api')).rejects.toThrow('Circuit breaker is OPEN for api');
        } finally {
            nowSpy.mockRestore();
        }
    });
});
