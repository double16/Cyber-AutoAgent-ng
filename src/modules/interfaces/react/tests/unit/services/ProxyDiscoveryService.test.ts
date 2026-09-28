import {describe, expect, it, jest} from '@jest/globals';
import {
    discoverHttpProxies,
    isPrivateIpv4,
    localProxyAddresses,
    preferPrivateProxyAddresses,
    probeKnownProxy,
} from '../../../src/services/ProxyDiscoveryService.js';

describe('ProxyDiscoveryService', () => {
    it('selects assigned private IPv4 addresses and then localhost', () => {
        const interfaces = {
            en0: [
                {family: 'IPv4', address: '192.168.1.5'},
                {family: 'IPv4', address: '8.8.8.8'},
                {family: 'IPv6', address: 'fd00::2'},
            ],
            bridge: [{family: 'IPv4', address: '172.20.0.2'}],
            lo: [{family: 'IPv4', address: '127.0.0.1'}],
        } as any;
        expect(localProxyAddresses(interfaces)).toEqual(['172.20.0.2', '192.168.1.5', '127.0.0.1']);
        expect(isPrivateIpv4('10.0.0.1')).toBe(true);
        expect(isPrivateIpv4('172.16.0.1')).toBe(true);
        expect(isPrivateIpv4('172.31.255.255')).toBe(true);
        for (const address of ['172.15.0.1', '172.32.0.1', '192.169.1.1', '127.0.0.1', '10.1.1.999']) {
            expect(isPrivateIpv4(address)).toBe(false);
        }
    });

    it('prefers a private binding over localhost for the same listener certificate', () => {
        const proxies = [
            {product: 'Burp Suite' as const, url: 'http://127.0.0.1:8080', fingerprint: 'cert-a'},
            {product: 'Burp Suite' as const, url: 'http://192.168.1.5:8080', fingerprint: 'cert-a'},
            {product: 'OWASP ZAP' as const, url: 'http://127.0.0.1:8080', fingerprint: 'cert-b'},
        ];
        expect(preferPrivateProxyAddresses(proxies).map(proxy => proxy.url)).toEqual([
            'http://127.0.0.1:8080',
            'http://192.168.1.5:8080',
        ]);
    });

    it.each([
        ['/cert', 'Burp Suite'],
        ['/OTHER/core/other/rootcert/', 'OWASP ZAP'],
        ['/OTHER/core/other/rootcert', 'OWASP ZAP'],
        ['/cert/pem', 'mitmproxy'],
        ['/cert/cer', 'mitmproxy'],
    ])('recognizes a certificate at %s as %s', async (path, product) => {
        const fetch = jest.fn(async (_host: string, _port: number, requestedPath: string, hostHeader: string) =>
            requestedPath === path && hostHeader !== '127.0.0.1:8080' ? 'cert-1' : null
        );
        const result = await probeKnownProxy('127.0.0.1', 8080, new AbortController().signal, fetch as any);
        expect(result).toEqual({product, url: 'http://127.0.0.1:8080', fingerprint: 'cert-1'});
        expect(fetch).toHaveBeenCalledWith('127.0.0.1', 8080, path, expect.any(String), expect.anything());
    });

    it('ignores listeners whose known paths do not return certificates', async () => {
        const fetch = jest.fn(async () => null);
        const result = await probeKnownProxy('127.0.0.1', 8081, new AbortController().signal, fetch as any);
        expect(result).toBeNull();
        expect(fetch).toHaveBeenCalledTimes(10);
    });

    it('scans only requested ports, skips closed listeners, and returns private addresses first', async () => {
        const connect = jest.fn(async (_host: string, port: number) => port === 8080);
        const probe = jest.fn(async (host: string, port: number) => ({
            product: 'Burp Suite' as const, url: `http://${host}:${port}`, fingerprint: 'same-cert',
        }));
        const result = await discoverHttpProxies({
            addresses: ['127.0.0.1', '192.168.1.5'], ports: [8080, 8081],
            connect: connect as any, probe: probe as any,
        });
        expect(connect).toHaveBeenCalledTimes(4);
        expect(probe).toHaveBeenCalledTimes(2);
        expect(result.map(proxy => proxy.url)).toEqual(['http://192.168.1.5:8080']);
    });

    it('returns no candidates after cancellation', async () => {
        const controller = new AbortController();
        controller.abort();
        const connect = jest.fn(async () => true);
        expect(await discoverHttpProxies({
            signal: controller.signal, addresses: ['127.0.0.1'], ports: [8080], connect: connect as any,
        })).toEqual([]);
        expect(connect).not.toHaveBeenCalled();
    });
});
