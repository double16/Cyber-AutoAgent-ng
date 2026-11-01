import {afterEach, expect, it, jest} from '@jest/globals';
import {ExecutionMode} from '../../../src/services/ExecutionService.js';
const services: any[] = [];
class Backend {
  isSupported = jest.fn<any>(); validate = jest.fn<any>(); cleanup = jest.fn();
  getCapabilities = () => ({canExecute: false, supportsStreaming: false, supportsParallel: true});
  constructor() {services.push(this); this.isSupported.mockResolvedValue(true); this.validate.mockResolvedValue({valid: true, issues: [], warnings: []});}
}
jest.unstable_mockModule('../../../src/services/PythonExecutionServiceAdapter.js', () => ({PythonExecutionServiceAdapter: Backend}));
jest.unstable_mockModule('../../../src/services/DockerExecutionServiceAdapter.js', () => ({DockerExecutionServiceAdapter: Backend}));
const {ExecutionServiceFactory} = await import('../../../src/services/ExecutionServiceFactory.js');
afterEach(() => {ExecutionServiceFactory.cleanup(); jest.restoreAllMocks();});
it('constructs all registered adapters and exposes parallel-only capabilities', async () => {
  for (const mode of [ExecutionMode.PYTHON_CLI, ExecutionMode.DOCKER_SINGLE, ExecutionMode.DOCKER_STACK]) {
    expect(await ExecutionServiceFactory.createService(mode)).toBeInstanceOf(Backend);
  }
  expect(await ExecutionServiceFactory.getModeCapabilities(ExecutionMode.DOCKER_STACK)).toEqual(['parallel']);
});
it('falls back after unsupported and invalid preferred services', async () => {
  const unsupported = new Backend(); unsupported.isSupported.mockResolvedValue(false);
  const invalid = new Backend(); invalid.validate.mockResolvedValue({valid: false, issues: [], warnings: []});
  const valid = new Backend();
  jest.spyOn(ExecutionServiceFactory, 'createService').mockResolvedValueOnce(unsupported as any).mockResolvedValueOnce(invalid as any).mockResolvedValueOnce(valid as any);
  const result = await ExecutionServiceFactory.selectService({} as any, {fallbackModes: [], validationTimeoutMs: 100} as any);
  expect(result.rejected).toHaveLength(2); expect(result.isPreferred).toBe(false);
  expect(unsupported.cleanup).toHaveBeenCalled(); expect(invalid.cleanup).toHaveBeenCalled();
});
