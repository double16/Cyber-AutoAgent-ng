import {jest} from '@jest/globals';
import {
  createError,
  CyberAgentError,
  ErrorCategory,
  Errors,
  ErrorSeverity,
  handleError,
  withErrorHandling,
} from '../../../src/utils/errorHandler.js';

let consoleSpies: Array<jest.SpiedFunction<any>> = [];
const originalEnv = {...process.env};

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
    process.env = {...originalEnv};
});

afterEach(() => {
    for (const spy of consoleSpies) {
        spy.mockRestore();
    }
    consoleSpies = [];
    process.env = {...originalEnv};
});

describe('CyberAgentError', () => {
    it('uses safe defaults and serializes to JSON', () => {
        const cause = new Error('root cause');
        const error = new CyberAgentError('something failed', {cause});
        const json = error.toJSON();

        expect(error.name).toBe('CyberAgentError');
        expect(error.severity).toBe(ErrorSeverity.MEDIUM);
        expect(error.category).toBe(ErrorCategory.UNKNOWN);
        expect(error.recoverable).toBe(true);
        expect(error.cause).toBe(cause);
        expect(json).toEqual(expect.objectContaining({
            name: 'CyberAgentError',
            message: 'something failed',
            severity: ErrorSeverity.MEDIUM,
            category: ErrorCategory.UNKNOWN,
            recoverable: true,
            cause,
        }));
        expect(typeof json.timestamp).toBe('string');
    });

    it('preserves explicit severity, category, context, and recoverability', () => {
        const context = {operation: 'scan', target: 'example.com'};
        const error = new CyberAgentError('bad config', {
            severity: ErrorSeverity.HIGH,
            category: ErrorCategory.CONFIGURATION,
            context,
            recoverable: false,
        });

        expect(error.severity).toBe(ErrorSeverity.HIGH);
        expect(error.category).toBe(ErrorCategory.CONFIGURATION);
        expect(error.context).toBe(context);
        expect(error.recoverable).toBe(false);
    });
});

describe('error factories', () => {
    it('creates correctly categorized common errors', () => {
        expect(Errors.network('network down')).toEqual(expect.objectContaining({
            category: ErrorCategory.NETWORK,
            severity: ErrorSeverity.HIGH,
            recoverable: true,
        }));
        expect(Errors.configuration('missing token')).toEqual(expect.objectContaining({
            category: ErrorCategory.CONFIGURATION,
            severity: ErrorSeverity.HIGH,
            recoverable: false,
        }));
        expect(Errors.timeout('slow request')).toEqual(expect.objectContaining({
            category: ErrorCategory.TIMEOUT,
            severity: ErrorSeverity.MEDIUM,
            recoverable: true,
        }));
        expect(Errors.validation('invalid input')).toEqual(expect.objectContaining({
            category: ErrorCategory.VALIDATION,
            severity: ErrorSeverity.LOW,
            recoverable: false,
        }));
        expect(Errors.permission('denied')).toEqual(expect.objectContaining({
            category: ErrorCategory.PERMISSION,
            severity: ErrorSeverity.CRITICAL,
            recoverable: false,
        }));
        expect(Errors.execution('tool failed')).toEqual(expect.objectContaining({
            category: ErrorCategory.EXECUTION,
            severity: ErrorSeverity.HIGH,
            recoverable: true,
        }));
    });

    it('creates medium severity errors with context', () => {
        const context = {module: 'web', step: 2};
        const error = createError('invalid target', ErrorCategory.VALIDATION, context);

        expect(error).toEqual(expect.objectContaining({
            message: 'invalid target',
            category: ErrorCategory.VALIDATION,
            severity: ErrorSeverity.MEDIUM,
            context,
        }));
    });
});

describe('handleError', () => {
    it('logs by severity and backfills CyberAgentError context', () => {
        const context = {operation: 'op-1'};
        const low = new CyberAgentError('low', {severity: ErrorSeverity.LOW});
        const medium = new CyberAgentError('medium', {severity: ErrorSeverity.MEDIUM});
        const high = new CyberAgentError('high', {severity: ErrorSeverity.HIGH});
        const critical = new CyberAgentError('critical', {severity: ErrorSeverity.CRITICAL});

        handleError(low, context);
        handleError(medium, context);
        handleError(high, context);
        handleError(critical, context);

        expect(low.context).toBe(context);
        expect(medium.context).toBe(context);
        expect(high.context).toBe(context);
        expect(critical.context).toBe(context);
        expect(console.info).toHaveBeenCalled();
        expect(console.warn).toHaveBeenCalled();
        expect(console.error).toHaveBeenCalled();
    });

    it('treats plain errors as high severity and optionally reports in production', () => {
        process.env.ENABLE_ERROR_REPORTING = 'true';
        process.env.NODE_ENV = 'production';

        handleError(new Error('plain failure'), {sessionId: 'abc'});

        expect(console.error).toHaveBeenCalled();
    });
});

describe('withErrorHandling', () => {
    it('returns successful async results unchanged', async () => {
        const fn = withErrorHandling(async (value: number) => value * 2, {operation: 'double'});

        await expect(fn(3)).resolves.toBe(6);
    });

    it('logs and rethrows async errors', async () => {
        const error = new Error('boom');
        const fn = withErrorHandling(async () => {
            throw error;
        }, {operation: 'explode'});

        await expect(fn()).rejects.toBe(error);
        expect(console.error).toHaveBeenCalled();
    });
});
