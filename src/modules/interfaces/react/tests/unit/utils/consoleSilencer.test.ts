import {disableConsoleSilence, enableConsoleSilence} from '../../../src/utils/consoleSilencer.js';
import {afterEach, beforeEach, describe, expect, it} from '@jest/globals';

describe('consoleSilencer', () => {
    let originals: typeof console;

    beforeEach(() => {
        originals = {
            ...console,
            log: console.log,
            info: console.info,
            debug: console.debug,
            warn: console.warn,
            error: console.error,
        };
    });

    afterEach(() => {
        disableConsoleSilence();
        console.log = originals.log;
        console.info = originals.info;
        console.debug = originals.debug;
        console.warn = originals.warn;
        console.error = originals.error;
    });

    it('silences log/info/debug/warn and leaves error intact', () => {
        enableConsoleSilence();

        expect(console.log).not.toBe(originals.log);
        expect(console.info).not.toBe(originals.info);
        expect(console.debug).not.toBe(originals.debug);
        expect(console.warn).not.toBe(originals.warn);
        expect(console.error).toBe(originals.error);
    });

    it('is idempotent and restores original console functions', () => {
        enableConsoleSilence();
        const silencedLog = console.log;

        enableConsoleSilence();
        expect(console.log).toBe(silencedLog);

        disableConsoleSilence();
        expect(console.log).toBe(originals.log);
        expect(console.info).toBe(originals.info);
        expect(console.debug).toBe(originals.debug);
        expect(console.warn).toBe(originals.warn);

        disableConsoleSilence();
        expect(console.log).toBe(originals.log);
    });
});

it('silences all output channels idempotently and restores their original functions', () => {
  const originals = [console.log, console.info, console.debug, console.warn];
  const error = console.error;
  disableConsoleSilence();
  try {
    enableConsoleSilence();
    enableConsoleSilence();
    console.log('ignored'); console.info('ignored'); console.debug('ignored'); console.warn('ignored');
    expect(console.error).toBe(error);
    expect(console.log).not.toBe(originals[0]);
  } finally {disableConsoleSilence();}
  expect([console.log, console.info, console.debug, console.warn]).toEqual(originals);
});
it('restores missing original channels without assigning an invalid function', () => {
  const originals = [console.log, console.info, console.debug, console.warn];
  try {
    [console.log, console.info, console.debug, console.warn] = [undefined, undefined, undefined, undefined] as any;
    enableConsoleSilence();
    disableConsoleSilence();
    expect(typeof console.log).toBe('function');
  } finally {[console.log, console.info, console.debug, console.warn] = originals;}
});
