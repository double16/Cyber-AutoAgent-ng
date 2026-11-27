import {PassThrough} from 'stream';
import {afterEach, beforeEach, expect, it, jest} from '@jest/globals';
const stream = () => new PassThrough();
let currentStream: PassThrough; let wait: any;
const container = {attach: jest.fn<any>(), start: jest.fn<any>(), wait: jest.fn<any>((cb: any) => {wait = cb;}), inspect: jest.fn<any>(), exec: jest.fn<any>(), stop: jest.fn<any>()};
const createContainer = jest.fn<any>(); const listContainers = jest.fn<any>();
const inspectNetwork = jest.fn<any>(); const inspectImage = jest.fn<any>(); const createNetwork = jest.fn<any>();
jest.unstable_mockModule('dockerode', () => ({default: jest.fn(() => ({createContainer, listContainers, getContainer: () => container, ping: async () => {}, getNetwork: () => ({inspect: inspectNetwork}), getImage: () => ({inspect: inspectImage}), createNetwork}))}));
jest.unstable_mockModule('child_process', () => ({execSync: () => ''}));
let mode = 'single-container';
jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({ContainerManager: {getInstance: () => ({getCurrentMode: async () => mode})}}));
const {DirectDockerService} = await import('../../../src/services/DirectDockerService.js');
beforeEach(() => {
  jest.useFakeTimers(); currentStream = stream(); mode = 'single-container';
  createContainer.mockResolvedValue(container); container.attach.mockResolvedValue(currentStream); container.start.mockResolvedValue(undefined);
  listContainers.mockResolvedValue([]); container.inspect.mockResolvedValue({Config: {}, NetworkSettings: {Networks: {test: {}}}});
  container.exec.mockResolvedValue({start: (_: any, cb: any) => cb(null, currentStream)});
  inspectNetwork.mockResolvedValue({}); inspectImage.mockResolvedValue({});
});
afterEach(() => {currentStream.destroy(); jest.useRealTimers(); jest.clearAllMocks();});
it.each(['single-container', 'local-cli', 'full-stack'])('streams a new %s assessment through completion and diagnostics', async deployment => {
  mode = deployment;
  const service = new DirectDockerService(); const events = jest.fn(); service.on('event', events); service.on('error', () => {});
  await service.executeAssessment({module: 'web', target: 'target'} as any, {mcp: {enabled: false}, outputDir: '/tmp', modelProvider: 'ollama'} as any);
  await expect(service.executeAssessment({module: 'web', target: 'target'} as any, {} as any)).rejects.toThrow('already running');
  await jest.advanceTimersByTimeAsync(2000);
  expect(events).toHaveBeenCalledWith(expect.objectContaining({content: expect.stringContaining('Preflight checks')}));
  currentStream.write('visible output\n'); currentStream.emit('error', new Error('stream failure'));
  (service as any).eventParser.emit('error', new Error('parser failure'));
  wait(new Error('wait failure')); wait(null, {StatusCode: 2}); wait(null, {StatusCode: 0});
  expect(events).toHaveBeenCalledWith(expect.objectContaining({content: expect.stringContaining('wait error')}));
  currentStream.emit('end'); expect(service.isAssessing()).toBe(false); service.cleanup();
});
it.each(['exact', 'label'])('reuses a running service container by %s and finalizes exec streams', async match => {
  listContainers.mockResolvedValue([{Id: 'id', State: 'running', Names: [match === 'exact' ? '/cyber-autoagent' : '/project-agent'], Labels: {'com.docker.compose.service': 'cyber-autoagent'}}]);
  mode = 'full-stack'; const service = new DirectDockerService(); const complete = jest.fn(); service.on('complete', complete);
  await service.executeAssessment({module: 'web', target: 'target'} as any, {mcp: {enabled: false}, outputDir: '/tmp'} as any);
  expect(container.exec).toHaveBeenCalledWith(expect.objectContaining({AttachStdin: true, Tty: true}));
  jest.advanceTimersByTime(600);
  currentStream.write('__CYBER_EVENT__'+JSON.stringify({type: 'operation_finalized'})+'__CYBER_EVENT_END__\n');
  currentStream.emit('end'); expect(service.isAssessing()).toBe(false); service.cleanup();
});
it('creates missing networks and rejects missing images', async () => {
  inspectNetwork.mockRejectedValueOnce(new Error('missing'));
  await DirectDockerService.ensureDockerResources(); expect(createNetwork).toHaveBeenCalledWith({Name: 'cyberagent-network', Driver: 'bridge'});
  inspectImage.mockRejectedValueOnce(new Error('missing'));
  await expect(DirectDockerService.ensureDockerResources()).rejects.toThrow('image');
});

it.each([true, false])('handles user-handoff auto-confirm with confirmations=%s', async confirmations => {
  const service = new DirectDockerService(); const writes = jest.spyOn(currentStream, 'write');
  await service.executeAssessment({module: 'web', target: 'target', evaluateOnly: true, operationId: 'OP_NEW', credentialRotationRequestId: 'req'} as any, {mcp: {enabled: false}, outputDir: '/tmp', confirmations, observability: true, enableLangfusePrompts: true, autoEvaluation: true} as any);
  (service as any).handleParsedDockerEvent({type: 'user_handoff'});
  await jest.advanceTimersByTimeAsync(1800);
  if (confirmations) expect(writes).not.toHaveBeenCalled(); else expect(writes).toHaveBeenCalledWith('execute\r\n');
  currentStream.emit('end'); service.dispose();
});
