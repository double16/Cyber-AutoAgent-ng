import {EventEmitter} from 'node:events';
import {X509Certificate} from 'node:crypto';
import {rootCertificates} from 'node:tls';
import {describe, expect, it, jest} from '@jest/globals';

let connectBehavior: () => any;
let requestBehavior: (options: any, callback: (response: any) => void) => any;
const createConnection = jest.fn((_options: any) => connectBehavior());
const request = jest.fn((options: any, callback: (response: any) => void) => requestBehavior(options, callback));

jest.unstable_mockModule('node:net', () => ({createConnection}));
jest.unstable_mockModule('node:http', () => ({request}));

const load = async () => import('../../../src/services/ProxyDiscoveryService.js');

function socket() {
    const value = new EventEmitter() as any;
    value.destroy = jest.fn();
    value.setTimeout = jest.fn((_: number, callback: () => void) => {
        value.timeout = callback;
    });
    return value;
}

function httpResponse(statusCode: number, body: Buffer) {
    const response = new EventEmitter() as any;
    response.statusCode = statusCode;
    response.resume = jest.fn();
    const req = new EventEmitter() as any;
    req.destroy = jest.fn(() => req.emit('error', new Error('closed')));
    req.setTimeout = jest.fn((_: number, callback: () => void) => {
        req.timeout = callback;
    });
    req.end = jest.fn();
    return {req, response};
}

function serveResponse(status: number, body: Buffer) {
    const {req, response} = httpResponse(status, body);
    requestBehavior = (_options, callback) => {
        req.end = jest.fn(() => {
            callback(response);
            response.emit('data', body);
            response.emit('end');
        });
        return req;
    };
    return {req, response};
}

describe('ProxyDiscoveryService network transport', () => {
    it('recognizes connected, refused, timed-out, and cancelled sockets', async () => {
        const {canConnect} = await load();
        let active = socket();
        connectBehavior = () => active;
        let result = canConnect('127.0.0.1', 8080, new AbortController().signal);
        active.emit('connect');
        expect(await result).toBe(true);
        expect(active.destroy).toHaveBeenCalled();

        active = socket();
        result = canConnect('127.0.0.1', 8081, new AbortController().signal);
        active.emit('error', new Error('refused'));
        expect(await result).toBe(false);

        active = socket();
        result = canConnect('127.0.0.1', 8082, new AbortController().signal);
        active.timeout();
        expect(await result).toBe(false);

        active = socket();
        const controller = new AbortController();
        result = canConnect('127.0.0.1', 8083, controller.signal);
        controller.abort();
        expect(await result).toBe(false);

        const alreadyAborted = new AbortController();
        alreadyAborted.abort();
        createConnection.mockClear();
        expect(await canConnect('127.0.0.1', 8084, alreadyAborted.signal)).toBe(false);
        expect(createConnection).not.toHaveBeenCalled();
    });

    it('accepts a direct HTTP 200 response containing a certificate', async () => {
        const {fetchProxyCertificate} = await load();
        const cert = Buffer.from(rootCertificates[0]);
        serveResponse(200, cert);

        const result = await fetchProxyCertificate('192.168.1.5', 8080, '/cert', 'burp', new AbortController().signal);
        expect(result).toBe(new X509Certificate(cert).fingerprint256);
        expect(request.mock.calls.at(-1)![0]).toEqual(expect.objectContaining({
            host: '192.168.1.5', port: 8080, path: '/cert', agent: false,
            headers: expect.objectContaining({Host: 'burp'}),
        }));
    });

    it.each([
        [404, Buffer.from(rootCertificates[0])],
        [200, Buffer.from('not a certificate')],
        [200, Buffer.alloc(65 * 1024)],
    ])('rejects status %s with a non-matching or oversized body', async (status, body) => {
        const {fetchProxyCertificate} = await load();
        const {req, response} = serveResponse(status, body);
        expect(await fetchProxyCertificate('127.0.0.1', 8080, '/cert', 'burp', new AbortController().signal))
            .toBeNull();
        if (status === 404) expect(response.resume).toHaveBeenCalled();
        if (body.length > 64 * 1024) expect(req.destroy).toHaveBeenCalled();
    });

    it('handles request timeout, abort, and transport errors', async () => {
        const {fetchProxyCertificate} = await load();
        let req = new EventEmitter() as any;
        req.destroy = jest.fn(() => req.emit('error', new Error('timeout')));
        req.setTimeout = jest.fn((_: number, callback: () => void) => { req.timeout = callback; });
        req.end = jest.fn();
        requestBehavior = (_options: any, _callback: any) => req;
        let result = fetchProxyCertificate('127.0.0.1', 8080, '/cert', 'burp', new AbortController().signal);
        req.timeout();
        expect(await result).toBeNull();

        req = new EventEmitter() as any;
        req.destroy = jest.fn();
        req.setTimeout = jest.fn();
        req.end = jest.fn();
        requestBehavior = (_options: any, _callback: any) => req;
        result = fetchProxyCertificate('127.0.0.1', 8080, '/cert', 'burp', new AbortController().signal);
        req.emit('error', new Error('refused'));
        expect(await result).toBeNull();

        const controller = new AbortController();
        controller.abort();
        request.mockClear();
        expect(await fetchProxyCertificate('127.0.0.1', 8080, '/cert', 'burp', controller.signal)).toBeNull();
        expect(request).not.toHaveBeenCalled();
    });
});
