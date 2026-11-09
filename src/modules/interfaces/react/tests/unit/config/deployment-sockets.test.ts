import {EventEmitter} from 'events';
import {afterEach, expect, it, jest} from '@jest/globals';
let signal = 'connect';
const destroy = jest.fn(); const timeout = jest.fn();
class Socket extends EventEmitter {
  setTimeout = timeout; destroy = destroy;
  connect(port: number, host: string) {expect(port).toBe(3000); expect(host).toBe('langfuse-web'); this.emit(signal);}
}
jest.unstable_mockModule('node:net', () => ({Socket}));
const {detectDeploymentMode} = await import('../../../src/config/deployment.js');
const container = process.env.CONTAINER;
afterEach(() => {if (container === undefined) delete process.env.CONTAINER; else process.env.CONTAINER = container;});
it.each(['connect', 'error', 'timeout'])('handles socket %s without real network access', async event => {
  process.env.CONTAINER = 'docker'; signal = event;
  const result = await detectDeploymentMode();
  expect(result.mode).toBe(event === 'connect' ? 'compose' : 'container');
  expect(timeout).toHaveBeenCalledWith(2000);
});
