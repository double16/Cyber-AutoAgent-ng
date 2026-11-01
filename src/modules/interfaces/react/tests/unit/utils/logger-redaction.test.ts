import {afterEach, expect, it, jest} from '@jest/globals';
import {Logger, createOperationLogger} from '../../../src/utils/logger.js';
const env = {...process.env};
afterEach(() => {process.env = {...env}; jest.restoreAllMocks();});
it('redacts nested secrets, truncates strings, and preserves non-string metadata', () => {
  const log = jest.spyOn(console, 'log').mockImplementation(() => {});
  process.env.CYBER_MAX_LOG_CHARS = '10';
  const logger = new Logger({level: 'debug', structured: true, component: 'test', operation: 'op', sessionId: 'session'});
  logger.info('long message with extra text', {metadata: {password: 'sensitive', nested: {token: 'secret'}, list: ['long string with extra text', null, 4, true], missing: undefined}});
  const output = JSON.parse(log.mock.calls[0][0] as string);
  expect(output.message).toContain('truncated');
  expect(output.metadata.password).toBe('***redacted***');
  expect(output.metadata.nested.token).toBe('***redacted***');
  expect(output.metadata.list[1]).toBeNull();
  expect(output.operation).toBe('op');
  logger.error('failed', new Error('failure'));
  expect(JSON.parse(log.mock.calls[1][0] as string).error.message).toBe('failure');
});
it('handles invalid log size configuration, empty messages, all levels and operation contexts', () => {
  const info = jest.spyOn(console, 'info').mockImplementation(() => {});
  const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
  jest.spyOn(console, 'error').mockImplementation(() => {});
  jest.spyOn(console, 'debug').mockImplementation(() => {});
  process.env.CYBER_MAX_LOG_CHARS = 'invalid';
  const logger = new Logger({level: 'debug', structured: false});
  logger.info(''); expect(info).not.toHaveBeenCalled();
  logger.info('visible', {metadata: {value: 3}}); logger.warn('warning'); logger.debug('debug'); logger.error('error', new Error('failure'));
  expect(info).toHaveBeenCalledWith(expect.stringContaining('Metadata:'));
  expect(warn).toHaveBeenCalledWith(expect.stringContaining('warning'));
  createOperationLogger('operation', 'session').info('context');
  expect(info).toHaveBeenLastCalledWith(expect.stringContaining('{operation}'));
});
it('suppresses production Ink logs unless debugging and supports non-TTY logging', () => {
  const info = jest.spyOn(console, 'info').mockImplementation(() => {});
  const tty = process.stdout.isTTY;
  try {
    process.env.NODE_ENV = 'production'; delete process.env.CYBER_TEST_MODE; delete process.env.DEBUG; delete process.env.CYBER_DEBUG;
    process.env.REACT_INK = 'true';
    const logger = new Logger({level: 'debug', structured: false}); logger.info('hidden');
    expect(info).not.toHaveBeenCalled();
    process.env.CYBER_DEBUG = '1'; logger.info('debugging'); expect(info).toHaveBeenCalledTimes(1);
    delete process.env.CYBER_DEBUG; delete process.env.REACT_INK; process.stdout.isTTY = false;
    logger.info('non-tty'); expect(info).toHaveBeenCalledTimes(2);
  } finally {process.stdout.isTTY = tty;}
});
