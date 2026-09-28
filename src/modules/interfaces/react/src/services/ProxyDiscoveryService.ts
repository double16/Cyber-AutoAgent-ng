import { X509Certificate } from 'node:crypto';
import { request } from 'node:http';
import { createConnection } from 'node:net';
import { networkInterfaces } from 'node:os';

export type ProxyProduct = 'Burp Suite' | 'OWASP ZAP' | 'mitmproxy';

export interface DiscoveredProxy {
  product: ProxyProduct;
  url: string;
  fingerprint: string;
}

interface ProxySignature {
  product: ProxyProduct;
  path: string;
  hostHeader: string;
}

const SIGNATURES: ProxySignature[] = [
  { product: 'Burp Suite', path: '/cert', hostHeader: 'burp' },
  { product: 'OWASP ZAP', path: '/OTHER/core/other/rootcert/', hostHeader: 'zap' },
  { product: 'OWASP ZAP', path: '/OTHER/core/other/rootcert', hostHeader: 'zap' },
  { product: 'mitmproxy', path: '/cert/pem', hostHeader: 'mitm.it' },
  { product: 'mitmproxy', path: '/cert/cer', hostHeader: 'mitm.it' },
];

const PORTS = Array.from({ length: 10 }, (_, index) => 8080 + index);
const CONNECT_TIMEOUT_MS = 250;
const REQUEST_TIMEOUT_MS = 750;
const DISCOVERY_TIMEOUT_MS = 5000;
const MAX_CERT_BYTES = 64 * 1024;

export function isPrivateIpv4(address: string): boolean {
  const parts = address.split('.').map(Number);
  if (parts.length !== 4 || parts.some(part => !Number.isInteger(part) || part < 0 || part > 255)) {
    return false;
  }
  return parts[0] === 10 ||
    (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) ||
    (parts[0] === 192 && parts[1] === 168);
}

/** Only inspect addresses assigned to this process's local network namespace. */
export function localProxyAddresses(interfaces = networkInterfaces()): string[] {
  const privateAddresses = Object.values(interfaces)
    .flatMap(entries => entries ?? [])
    .filter(entry => entry.family === 'IPv4' && isPrivateIpv4(entry.address))
    .map(entry => entry.address);
  return [...new Set(privateAddresses)].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .concat('127.0.0.1');
}

export function preferPrivateProxyAddresses(proxies: DiscoveredProxy[]): DiscoveredProxy[] {
  const preferred = new Map<string, DiscoveredProxy>();
  for (const proxy of proxies) {
    const url = new URL(proxy.url);
    const key = `${url.port}:${proxy.fingerprint}`;
    const existing = preferred.get(key);
    if (!existing || (url.hostname !== '127.0.0.1' && new URL(existing.url).hostname === '127.0.0.1')) {
      preferred.set(key, proxy);
    }
  }
  return [...preferred.values()].sort((a, b) => a.url.localeCompare(b.url, undefined, { numeric: true }));
}

export function canConnect(host: string, port: number, signal: AbortSignal): Promise<boolean> {
  return new Promise(resolve => {
    if (signal.aborted) return resolve(false);
    const socket = createConnection({ host, port });
    let settled = false;
    const onAbort = () => finish(false);
    const finish = (connected: boolean) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', onAbort);
      socket.destroy();
      resolve(connected);
    };
    socket.setTimeout(CONNECT_TIMEOUT_MS, () => finish(false));
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

/** Request the known certificate path directly, ignoring proxy environment variables. */
export function fetchProxyCertificate(
  host: string, port: number, path: string, hostHeader: string, signal: AbortSignal
): Promise<string | null> {
  return new Promise(resolve => {
    if (signal.aborted) return resolve(null);
    let settled = false;
    const finish = (fingerprint: string | null) => {
      if (settled) return;
      settled = true;
      resolve(fingerprint);
    };
    const req = request({
      host, port, path, method: 'GET', agent: false, signal,
      headers: { Host: hostHeader, 'User-Agent': 'CyberAutoAgent/1.0', Accept: '*/*' },
    }, response => {
      if (response.statusCode !== 200) {
        response.resume();
        finish(null);
        return;
      }
      const chunks: Buffer[] = [];
      let size = 0;
      response.on('data', (chunk: Buffer) => {
        size += chunk.length;
        if (size > MAX_CERT_BYTES) {
          req.destroy();
          finish(null);
        } else {
          chunks.push(chunk);
        }
      });
      response.on('end', () => {
        if (settled) return;
        try {
          finish(new X509Certificate(Buffer.concat(chunks)).fingerprint256);
        } catch {
          finish(null);
        }
      });
      response.on('error', () => finish(null));
    });
    req.setTimeout(REQUEST_TIMEOUT_MS, () => req.destroy());
    req.once('error', () => finish(null));
    req.end();
  });
}

export async function probeKnownProxy(
  host: string,
  port: number,
  signal: AbortSignal,
  fetchCertificate = fetchProxyCertificate
): Promise<DiscoveredProxy | null> {
  for (const signature of SIGNATURES) {
    for (const hostHeader of [`${host}:${port}`, signature.hostHeader]) {
      if (signal.aborted) return null;
      const fingerprint = await fetchCertificate(host, port, signature.path, hostHeader, signal);
      if (fingerprint) {
        return { product: signature.product, url: `http://${host}:${port}`, fingerprint };
      }
    }
  }
  return null;
}

export interface DiscoveryOptions {
  signal?: AbortSignal;
  addresses?: string[];
  ports?: number[];
  connect?: typeof canConnect;
  probe?: typeof probeKnownProxy;
}

export async function discoverHttpProxies(options: DiscoveryOptions = {}): Promise<DiscoveredProxy[]> {
  const signal = options.signal
    ? AbortSignal.any([options.signal, AbortSignal.timeout(DISCOVERY_TIMEOUT_MS)])
    : AbortSignal.timeout(DISCOVERY_TIMEOUT_MS);
  const addresses = options.addresses ?? localProxyAddresses();
  const ports = options.ports ?? PORTS;
  const connect = options.connect ?? canConnect;
  const probe = options.probe ?? probeKnownProxy;
  const candidates = await Promise.all(addresses.flatMap(host => ports.map(async port => {
    if (signal.aborted) return null;
    if (!await connect(host, port, signal)) return null;
    return probe(host, port, signal);
  })));
  return preferPrivateProxyAddresses(candidates.filter((item): item is DiscoveredProxy => item !== null));
}
