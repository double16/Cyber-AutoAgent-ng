import {flattenEnvironment} from '../../../src/utils/env.js';

describe('flattenEnvironment', () => {
    it('returns an empty map for missing or invalid environments', () => {
        expect(flattenEnvironment()).toEqual({});
        expect(flattenEnvironment(null)).toEqual({});
    });

    it('sanitizes keys and stringifies primitive values', () => {
        expect(flattenEnvironment({
            'api-key': 'secret',
            'max retries': 3,
            enabled: true,
            disabled: false,
        })).toEqual({
            API_KEY: 'secret',
            MAX_RETRIES: '3',
            ENABLED: 'true',
            DISABLED: 'false',
        });
    });

    it('flattens nested objects with sanitized path segments', () => {
        expect(flattenEnvironment({
            provider: {
                'base-url': 'http://localhost:11434',
                auth: {
                    token: 'abc',
                },
            },
        })).toEqual({
            PROVIDER_BASE_URL: 'http://localhost:11434',
            PROVIDER_AUTH_TOKEN: 'abc',
        });
    });

    it('preserves arrays as JSON strings', () => {
        expect(flattenEnvironment({
            models: ['llama3', 'qwen'],
            nested: {ports: [8080, 9000]},
        })).toEqual({
            MODELS: '["llama3","qwen"]',
            NESTED_PORTS: '[8080,9000]',
        });
    });

    it('skips null, undefined, empty object, and empty sanitized keys', () => {
        expect(flattenEnvironment({
            value: null,
            missing: undefined as any,
            emptyObject: {},
            '---': 'ignored',
            keep: 'yes',
        })).toEqual({
            KEEP: 'yes',
        });
    });
});
