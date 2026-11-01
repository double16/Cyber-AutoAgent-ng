import {EventEmitter} from 'events';
import {afterEach, beforeEach, expect, it, jest} from '@jest/globals';
import {ExecutionMode} from '../../../src/services/ExecutionService.js';
const mkdir = jest.fn<any>(); const writeFile = jest.fn<any>(); const unlink = jest.fn<any>(); const existsSync = jest.fn<any>();
const inspect = jest.fn<any>();
const exec = (command: string, cb: any) => {inspect(command).then((value: any) => cb(null, value), (error: any) => cb(error));};
jest.unstable_mockModule('fs/promises', () => ({mkdir, writeFile, unlink}));
jest.unstable_mockModule('fs', () => ({existsSync}));
jest.unstable_mockModule('node:child_process', () => ({exec}));
const services: Backend[] = [];
class Backend extends EventEmitter {
  static checkDocker = jest.fn<any>();
  checkPythonVersion = jest.fn<any>(); checkEnvironmentStatus = jest.fn<any>();
  executeAssessment = jest.fn<any>(); stop = jest.fn<any>(); cleanup = jest.fn(); clearRuntimeState = jest.fn();
  isActive = () => false; isAssessing = () => false;
  projectRoot = '/project';
  constructor() {super(); services.push(this);}
}
const manager = new EventEmitter() as any;
manager.checkContainerStatus = jest.fn<any>(); manager.switchToMode = jest.fn<any>();
jest.unstable_mockModule('../../../src/services/PythonExecutionService.js', () => ({PythonExecutionService: Backend}));
jest.unstable_mockModule('../../../src/services/DirectDockerService.js', () => ({DirectDockerService: Backend}));
jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({ContainerManager: {getInstance: () => manager}}));
const {PythonExecutionServiceAdapter} = await import('../../../src/services/PythonExecutionServiceAdapter.js');
const {DockerExecutionServiceAdapter} = await import('../../../src/services/DockerExecutionServiceAdapter.js');
const env = {...process.env};
beforeEach(() => {
  jest.clearAllMocks(); delete process.env.CONTAINER; delete process.env.IS_DOCKER; delete process.env.DEV;
  existsSync.mockReturnValue(false); mkdir.mockResolvedValue(undefined); writeFile.mockResolvedValue(undefined); unlink.mockResolvedValue(undefined);
  inspect.mockResolvedValue({stdout: ''}); Backend.checkDocker.mockResolvedValue(true);
  manager.checkContainerStatus.mockResolvedValue({requiredContainers: {'full-stack': {missing: [], needsRestart: []}, 'single-container': {missing: [], needsRestart: []}}});
});
afterEach(() => {process.env = {...env}; manager.removeAllListeners();});
it.each(['good', 'corrupted', 'missing-python', 'filesystem', 'exception', 'string-error'])('validates host Python scenario %s', async scenario => {
  const adapter = new PythonExecutionServiceAdapter(); const service = services.at(-1)!;
  service.checkPythonVersion.mockResolvedValue({installed: scenario !== 'missing-python'});
  service.checkEnvironmentStatus.mockResolvedValue({venvExists: true, venvValid: scenario !== 'corrupted', dependenciesInstalled: true, packageInstalled: true});
  if (scenario === 'filesystem') mkdir.mockRejectedValue(new Error('readonly'));
  if (scenario === 'exception' || scenario === 'string-error') service.checkPythonVersion.mockRejectedValue(scenario === 'exception' ? new Error('boom') : 'boom');
  const result = await adapter.validate({modelProvider: 'bedrock', awsBearerToken: 'fixture', outputDir: './outputs'} as any);
  expect(result.valid).toBe(scenario === 'good');
  if (scenario === 'corrupted') expect(result.issues).toContainEqual(expect.objectContaining({message: 'Virtual environment exists but is corrupted'}));
  adapter.cleanup();
});
it('resolves Python failures, relays errors, and clears completed runtime without stopping it', async () => {
  const adapter = new PythonExecutionServiceAdapter(); const service = services.at(-1)!;
  const error = jest.fn(); adapter.on('error', error);
  service.executeAssessment.mockRejectedValue('failure');
  const handle = await adapter.execute({} as any, {} as any);
  await expect(handle.result).resolves.toMatchObject({success: false, error: 'failure'});
  expect(error).toHaveBeenCalledWith('failure');
  service.emit('complete'); adapter.cleanup();
  expect(service.clearRuntimeState).toHaveBeenCalled();
  service.stop.mockRejectedValue(new Error('already stopped'));
  const other = new PythonExecutionServiceAdapter(); services.at(-1)!.stop.mockRejectedValue(new Error('gone')); other.emit('stop');
  await other.stop().catch(() => {}); other.cleanup();
});
it.each(['good', 'missing-image', 'development', 'filesystem', 'credentials', 'exception', 'string-error'])('validates Docker scenario %s', async scenario => {
  const adapter = new DockerExecutionServiceAdapter(ExecutionMode.DOCKER_STACK);
  if (scenario === 'missing-image' || scenario === 'development') inspect.mockRejectedValue(new Error('missing'));
  if (scenario === 'development') process.env.DEV = 'true';
  if (scenario === 'filesystem') writeFile.mockRejectedValue(new Error('readonly'));
  if (scenario === 'exception' || scenario === 'string-error') Backend.checkDocker.mockRejectedValue(scenario === 'exception' ? new Error('offline') : 'offline');
  existsSync.mockImplementation((p: string) => p.endsWith('pyproject.toml'));
  const result = await adapter.validate({modelProvider: 'bedrock', awsAccessKeyId: scenario === 'credentials' ? undefined : 'fixture'} as any);
  expect(result.valid).toBe(scenario === 'good' || scenario === 'development');
  adapter.cleanup();
});
