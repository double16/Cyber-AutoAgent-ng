import {isNonEmptyString, isObject, toSafeString, truncate} from '../../../src/utils/typeUtils.js';

describe('typeUtils', () => {
    it('detects non-empty strings after trimming', () => {
        expect(isNonEmptyString(' value ')).toBe(true);
        expect(isNonEmptyString('   ')).toBe(false);
        expect(isNonEmptyString(123)).toBe(false);
    });

    it('detects plain non-null non-array objects', () => {
        expect(isObject({a: 1})).toBe(true);
        expect(isObject(null)).toBe(false);
        expect(isObject(['a'])).toBe(false);
        expect(isObject('x')).toBe(false);
    });

    it('safely converts values to strings', () => {
        expect(toSafeString(null)).toBe('');
        expect(toSafeString(undefined)).toBe('');
        expect(toSafeString('text')).toBe('text');
        expect(toSafeString(42)).toBe('42');
        expect(toSafeString({a: 1})).toBe('{"a":1}');

        const cyclic: any = {};
        cyclic.self = cyclic;
        expect(toSafeString(cyclic)).toBe('[object Object]');
    });

    it('truncates only when strings exceed max length', () => {
        expect(truncate('abc', 5)).toBe('abc');
        expect(truncate('abcdef', 3)).toBe('abc...');
    });
});
