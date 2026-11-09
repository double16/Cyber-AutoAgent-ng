import {EventEmitter} from 'events';
import {afterEach, expect, it, jest} from '@jest/globals';
let child: any;
const existsSync = jest.fn(() => true);
const spawn = jest.fn(() => {
  child = new EventEmitter(); child.pid = 12345; child.killed = false;
  child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.stdin = {write: jest.fn((_: string, cb: any) => cb())}; child.kill = jest.fn(); return child;
});
const exec = jest.fn((...args: any[]) => args.at(-1)(null, {stdout: 'Python 3.12.0'}));
jest.unstable_mockModule('fs', () => ({existsSync}));
jest.unstable_mockModule('child_process', () => ({spawn, exec, execFileSync: () => Buffer.from('Python 3.12.0')}));
const {PythonExecutionService} = await import('../../../src/services/PythonExecutionService.js');
afterEach(() => {jest.useRealTimers(); jest.restoreAllMocks(); existsSync.mockReturnValue(true);});
it.each([
  ["ModuleNotFoundError: No module named 'missing'", 'Missing Python dependency: missing'],
  ['ImportError', 'Missing Python dependency'], ['SyntaxError', 'Python syntax error'],
  ['PermissionError', 'Permission denied'], ["FileNotFoundError: 'config.yaml'", 'Required file not found: config.yaml'],
  ['FileNotFoundError', 'Required file not found'], ['CUDA', 'PyTorch/CUDA'], ['', 'crashed without error output'], ['other', 'exited with code'],
])('reports process diagnostics for %s', async (stderr, message) => {
  jest.useFakeTimers(); const service = new PythonExecutionService(); service.on('error', () => {});
  const events = jest.fn(); service.on('event', events);
  const promise = service.executeAssessment({module: 'web', target: 'example.com'} as any, {outputDir: '/outputs'} as any);
  const checked = expect(promise).rejects.toThrow(message);
  child.stderr.emit('data', Buffer.from('padding'.repeat(1500) + '\n' + stderr));
  if (!stderr) (service as any).stderrBuffer = '';
  child.emit('exit', 1, null); await checked;
  expect(events).toHaveBeenCalledWith(expect.objectContaining({type: 'error', error: expect.stringContaining(message)}));
  service.cleanup();
});
it('emits startup progress, relays stdout, accepts input and completes cleanly', async () => {
  jest.useFakeTimers(); const service = new PythonExecutionService(); const events = jest.fn(); service.on('event', events);
  const promise = service.executeAssessment({module: 'web', target: 'example.com', evaluateOnly: 'OP_OLD', operationId: 'OP_NOW', credentialRotationRequestId: 'req'} as any, {qdrantUrl: 'http://memory', outputDir: '/outputs'} as any);
  await expect(service.executeAssessment({} as any, {} as any)).rejects.toThrow('already running');
  await jest.advanceTimersByTimeAsync(2000);
  expect(events).toHaveBeenCalledWith(expect.objectContaining({content: expect.stringContaining('Qdrant memory: http://memory')}));
  child.stdout.emit('data', Buffer.from('visible output\n'));
  await service.sendUserInput('yes'); expect(child.stdin.write).toHaveBeenCalledWith('yes\n', expect.any(Function));
  await service.sendUserInput('yes\n');
  child.stdin.write.mockImplementation((_: string, cb: any) => cb(new Error('closed')));
  await expect(service.sendUserInput('x')).rejects.toThrow('closed');
  child.stdin.write.mockImplementation(() => {throw new Error('broken');});
  await expect(service.sendUserInput('x')).rejects.toThrow('broken');
  child.emit('exit', 0); await promise;
  expect(service.isActive()).toBe(false); expect(jest.getTimerCount()).toBe(0);
  await expect(service.sendUserInput('x')).rejects.toThrow('No active'); service.cleanup();
});
it('handles process startup errors, absent environments and intentional termination', async () => {
  jest.useFakeTimers(); const service = new PythonExecutionService(); service.on('error', () => {});
  let promise = service.executeAssessment({module: 'web', target: 'target'} as any, {} as any);
  const checked = expect(promise).rejects.toThrow('spawn failure'); child.emit('error', new Error('spawn failure')); await checked;
  existsSync.mockReturnValue(false); await expect(service.executeAssessment({module: 'web', target: 'target'} as any, {} as any)).rejects.toThrow('environment not set up');
  existsSync.mockReturnValue(true);
  promise = service.executeAssessment({module: 'web', target: 'target'} as any, {} as any);
  const kill = jest.spyOn(process, 'kill').mockImplementation(() => {throw new Error('no group');});
  await service.stop(); expect(child.kill).toHaveBeenCalledWith('SIGTERM');
  jest.advanceTimersByTime(3000); expect(child.kill).toHaveBeenCalledWith('SIGKILL');
  child.emit('exit', 1, 'SIGTERM'); await promise; kill.mockRestore(); service.cleanup();
});
