import {afterEach, beforeEach, expect, it, jest} from '@jest/globals';
const existsSync = jest.fn(); const readFileSync = jest.fn(); const writeFileSync = jest.fn();
jest.unstable_mockModule('fs', () => ({default: {existsSync, readFileSync, writeFileSync}}));
const {TestExecutionService} = await import('../../../src/services/TestExecutionService.js');
const env = {...process.env};
beforeEach(() => {jest.useFakeTimers(); jest.clearAllMocks(); process.env.CYBER_TEST_EVENT_INTERVAL_MS = '2';});
afterEach(() => {process.env = {...env}; jest.useRealTimers(); jest.restoreAllMocks();});
it('streams JSONL fixtures, samples memory, and writes a completion summary', async () => {
  process.env.CYBER_TEST_EVENTS_PATH = '/events'; process.env.CYBER_TEST_MEMORY_LOG_PATH = '/memory';
  existsSync.mockReturnValue(true); readFileSync.mockReturnValue('{"type":"output","content":"fixture"}\n');
  const service = new TestExecutionService(); const events = jest.fn(); service.on('event', events);
  const progress = jest.fn(); await service.setup({}, progress); expect(progress).toHaveBeenCalled();
  const handle = await service.execute({}, {});
  jest.advanceTimersByTime(20);
  await expect(handle.result).resolves.toMatchObject({success: true});
  expect(events).toHaveBeenCalledWith({type: 'output', content: 'fixture'});
  expect(writeFileSync).toHaveBeenCalledWith('/memory', expect.stringContaining('peakRssBytes'));
  expect(handle.isActive()).toBe(false); service.cleanup(); await service.stop();
});
it('falls back on invalid fixture JSON and stops an active stream safely', async () => {
  process.env.CYBER_TEST_EVENTS_PATH = '/bad'; existsSync.mockReturnValue(true); readFileSync.mockReturnValue('invalid');
  process.env.CYBER_TEST_EVENT_INTERVAL_MS = '-1';
  const service = new TestExecutionService(); const stopped = jest.fn(); service.on('stopped', stopped);
  await service.setup({}); const handle = await service.execute({}, {});
  expect(handle.isActive()).toBe(true); await service.stop(); await service.stop();
  expect(stopped).toHaveBeenCalledTimes(1); service.cleanup();
});
it('tolerates failed memory collection and unwritable memory logs', async () => {
  process.env.CYBER_TEST_MEMORY_LOG_PATH = '/memory';
  delete process.env.CYBER_TEST_EVENTS_PATH;
  jest.spyOn(process, 'memoryUsage').mockImplementation(() => {throw new Error('unavailable');});
  writeFileSync.mockImplementation(() => {throw new Error('readonly');});
  const service = new TestExecutionService(); const handle = await service.execute({}, {});
  jest.advanceTimersByTime(100);
  await expect(handle.result).resolves.toMatchObject({success: true});
  service.cleanup();
});
