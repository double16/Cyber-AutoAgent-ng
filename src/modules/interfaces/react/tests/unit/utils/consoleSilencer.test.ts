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
