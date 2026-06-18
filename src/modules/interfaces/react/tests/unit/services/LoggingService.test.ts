import {afterEach, describe, expect, it, jest} from '@jest/globals';

const logger = {
    debug: jest.fn(),
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
};

jest.unstable_mockModule('../../../src/utils/logger.js', () => ({
    logger,
}));

const load = async () => import('../../../src/services/LoggingService.js');

describe('LoggingService', () => {
    const originalNodeEnv = process.env.NODE_ENV;
    const originalLogLevel = process.env.LOG_LEVEL;
    const originalConsole = {
        log: console.log,
        info: console.info,
        warn: console.warn,
        error: console.error,
        debug: console.debug,
    };

    afterEach(() => {
        process.env.NODE_ENV = originalNodeEnv;
        if (originalLogLevel === undefined) {
            delete process.env.LOG_LEVEL;
        } else {
            process.env.LOG_LEVEL = originalLogLevel;
        }
        console.log = originalConsole.log;
        console.info = originalConsole.info;
        console.warn = originalConsole.warn;
        console.error = originalConsole.error;
        console.debug = originalConsole.debug;
        Object.values(logger).forEach(mock => mock.mockClear());
        jest.resetModules();
    });

    it('buffers log entries, honors log levels, formats objects and errors, and creates context loggers', async () => {
        process.env.NODE_ENV = 'test';
        process.env.LOG_LEVEL = 'WARN';
        const {LoggingService, LogLevel, componentLoggers} = await load();
        const service = (LoggingService as any).getInstance();

        service.clearBuffer();
        service.debug('debug', {hidden: true});
        service.info('info');
        service.warn('warn', {visible: true});
        service.error('boom', new Error('failure'));
        componentLoggers.dockerService.warn('context message');

        expect(logger.debug).not.toHaveBeenCalled();
        expect(logger.info).not.toHaveBeenCalled();
        expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining('warn'));
        expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining('[DockerService]'));
        expect(logger.error).toHaveBeenCalledWith(expect.stringContaining('failure'));

        const recent = service.getRecentLogs(3);
        expect(recent).toHaveLength(3);
        expect(recent[0].level).toBe('WARN');
        expect(recent[0].message).toContain('"visible": true');
        expect(recent[1].level).toBe('ERROR');
        expect(recent[1].message).toContain('failure');

        service.setLogLevel(LogLevel.DEBUG);
        service.debug('now visible');
        expect(logger.debug).toHaveBeenCalledWith('now visible');

        service.clearBuffer();
        expect(service.getRecentLogs()).toEqual([]);
    });

    it('defaults to error logging in production and replaces console methods', async () => {
        process.env.NODE_ENV = 'production';
        delete process.env.LOG_LEVEL;
        const {LoggingService} = await load();
        const service = (LoggingService as any).getInstance();

        console.info('prod info suppressed');
        console.error('prod error visible');

        expect(logger.info).not.toHaveBeenCalledWith('prod info suppressed');
        expect(logger.error).toHaveBeenCalledWith('prod error visible');
        expect(console.log).not.toBe(originalConsole.log);
    });
});
