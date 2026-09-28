import {describe, expect, it} from '@jest/globals';
import {resolveHttpProxyEnvironment} from '../../../src/utils/httpProxy.js';

describe('resolveHttpProxyEnvironment', () => {
    it('sets both HTTP and HTTPS spellings from one URL', () => {
        const expected = {
            http_proxy: 'http://proxy.example:8080',
            https_proxy: 'http://proxy.example:8080',
            HTTP_PROXY: 'http://proxy.example:8080',
            HTTPS_PROXY: 'http://proxy.example:8080',
        };
        expect(resolveHttpProxyEnvironment(' http://proxy.example:8080 ')).toEqual(expected);
        expect(resolveHttpProxyEnvironment('https://proxy.example:8443').https_proxy)
            .toBe('https://proxy.example:8443');
    });

    it('leaves inherited environment alone when the setting is empty', () => {
        expect(resolveHttpProxyEnvironment()).toEqual({});
        expect(resolveHttpProxyEnvironment('  ')).toEqual({});
    });

    it.each(['proxy.example:8080', 'socks5://proxy.example:1080', 'http://', 'http:///'])
    ('rejects an invalid or unsupported URL: %s', value => {
        expect(() => resolveHttpProxyEnvironment(value)).toThrow('Invalid HTTP proxy URL');
    });
});
