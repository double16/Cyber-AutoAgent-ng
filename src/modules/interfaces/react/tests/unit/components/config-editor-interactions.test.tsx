import React from 'react';
import {afterEach, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let config: any; let handler: any; let editor: any; const selects: any[] = [];
const updateConfig = jest.fn((updates: any) => {config = {...config, ...updates};});
const saveConfig = jest.fn<any>();
jest.unstable_mockModule('ink', () => ({Box: ({children}: any) => <div>{children}</div>, Text: ({children}: any) => <span>{children}</span>, useInput: (cb: any, options: any) => {if (options?.isActive !== false) handler = cb;}}));
jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({useConfig: () => ({config, updateConfig, saveConfig})}));
const Input = (props: any) => {
  if (props.items) {selects.push(props); props.indicatorComponent?.({isSelected: true}); props.indicatorComponent?.({isSelected: false}); props.itemComponent?.({isSelected: true, label: 'choice'}); props.itemComponent?.({isSelected: false, label: 'choice'});}
  else editor = props;
  return <span>editing</span>;
};
jest.unstable_mockModule('ink-text-input', () => ({default: Input, UncontrolledTextInput: Input}));
jest.unstable_mockModule('../../../src/components/TokenInput.js', () => ({TokenInput: Input}));
jest.unstable_mockModule('../../../src/components/PasswordInput.js', () => ({PasswordInput: Input}));
jest.unstable_mockModule('../../../src/services/ProxyDiscoveryService.js', () => ({discoverHttpProxies: async () => []}));
const {ConfigEditor} = await import('../../../src/components/ConfigEditor.js');
let view: ReturnType<typeof TestRenderer.create> | undefined;
const send = (input = '', key = {}) => act(() => handler(input, key));
async function open(section: number, index: number, overrides = {}) {
  if (view) act(() => view!.unmount());
  config = {deploymentMode: 'full-stack', modelProvider: 'bedrock', modelId: 'claude-opus-4', observability: false, autoEvaluation: false, mcp: {enabled: true, connections: [{id: 'a', transport: 'streamable_http', server_url: 'http://mcp', command: ['node'], headers: {Authorization: 'fixture'}, plugins: ['web', 'ctf'], allowedTools: ['shell', 'editor'], timeoutSeconds: 10}]}, ...overrides};
  editor = undefined; selects.length = 0;
  await act(async () => {view = TestRenderer.create(<ConfigEditor onClose={jest.fn()}/>);});
  for (let i = 0; i < section; i++) send('', {downArrow: true});
  send('', {return: true});
  for (let i = 0; i < index; i++) send('', {downArrow: true});
  send('', {return: true});
}
afterEach(() => {if (view) act(() => view!.unmount()); view = undefined; jest.useRealTimers(); jest.clearAllMocks();});
it('edits numeric, boolean, secret, text, select and model pricing settings', async () => {
  // Explicit UI fields: duration, concurrency, credentials, service URL, evaluation threshold and pricing.
  for (const [section, index] of [[1,0], [1,3], [1,8], [1,9], [2,0], [2,1], [2,2], [2,3], [3,0], [3,1], [3,2], [3,3], [3,4], [4,0], [4,1], [5,0], [5,1], [7,0], [7,1]]) {
    await open(section, index);
    if (editor) {act(() => editor.onChange?.('42')); act(() => editor.onSubmit('42'));}
    else if (selects.length) act(() => selects.at(-1).onSelect(selects.at(-1).items[0]));
  }
  expect(updateConfig).toHaveBeenCalledWith(expect.objectContaining({modelPricing: expect.any(Object)}));
  expect(updateConfig).toHaveBeenCalledWith({qdrantApiKey: '42'});
  expect(updateConfig).toHaveBeenCalledWith({outputDir: '42'});
});
it.each(['ollama', 'litellm'])('switches to %s with provider model defaults', async provider => {
  await open(0,0); const select = selects.at(-1);
  act(() => select.onSelect(select.items.find((item: any) => item.value === provider)));
  expect(updateConfig).toHaveBeenLastCalledWith(expect.objectContaining({modelProvider: provider, temperature: null, embeddingModel: expect.any(String)}));
});
it('cleans bearer and API credentials and saves numeric edits via keyboard', async () => {
  await open(0,10, {awsBearerToken: 'existing'});
  act(() => editor.onSubmit(' ab c\n+/=!@ ')); expect(updateConfig).toHaveBeenLastCalledWith({awsBearerToken: 'abc+/='});
  await open(0,8, {awsAccessKeyId: 'existing'});
  act(() => editor.onSubmit(' a b\n')); expect(updateConfig).toHaveBeenLastCalledWith({awsAccessKeyId: 'ab'});
  await open(1,0); act(() => editor.onChange('12')); send('s', {ctrl: true});
  expect(updateConfig).toHaveBeenCalledWith({budgetMaxDuration: 12});
});
it('edits MCP identities, transports, JSON command/headers, timeout and free-form lists', async () => {
  for (const [index, value] of [[2, 'renamed'], [4, 'http://new-mcp'], [5, '["python","server.py"]'], [6, '{"X-Test":"value"}'], [9, '30']] as const) {
    await open(6,index);
    act(() => editor.onChange(value)); act(() => editor.onSubmit('invalid'));
    if ([5,6,9].includes(index)) expect(document.body.textContent).toMatch(/Invalid JSON|valid number/);
    // Scalar strings accept any string; reopen before submitting the valid value.
    if ([2,4].includes(index)) await open(6,index);
    act(() => editor.onSubmit(value));
  }
  await open(6,3); act(() => selects.at(-1).onSelect({value: 'sse'}));
  expect(config.mcp.connections[0].transport).toBe('sse');
  for (const index of [7,8]) {
    await open(6,index);
    act(() => editor.onChange('new')); act(() => editor.onSubmit('new'));
    expect(config.mcp.connections[0][index === 7 ? 'plugins' : 'allowedTools']).toContain('new');
    act(() => selects.at(-1).onSelect(selects.at(-1).items[0]));
    act(() => editor.onSubmit('*'));
    expect(config.mcp.connections[0][index === 7 ? 'plugins' : 'allowedTools']).toEqual(['*']);
    act(() => editor.onSubmit(' '));
    act(() => selects.at(-1).onSelect({value: '*'}));
  }
});
it('tests MCP transport failures and fallback POST success', async () => {
  const original = globalThis.fetch;
  try {
    for (const [transport, server_url, fetch, expected] of [
      ['stdio', '', undefined, 'N/A for stdio'],
      ['sse', '', undefined, 'Missing server_url'],
      ['sse', 'http://mcp', undefined, 'Fetch API unavailable'],
      ['sse', 'http://mcp', jest.fn<any>().mockRejectedValue(new Error('offline')), 'FAILED: offline'],
      ['streamable-http', 'http://mcp', jest.fn<any>().mockResolvedValueOnce({status: 500}).mockResolvedValueOnce({status: 200}), 'OK (200)'],
    ] as const) {
      globalThis.fetch = fetch as any;
      await open(6,10, {mcp: {enabled: true, connections: [{id: 'a', transport, server_url}]}});
      await act(async () => {await Promise.resolve();});
      expect(document.body.textContent).toContain(expected);
    }
  } finally {globalThis.fetch = original;}
});

it('renders configured model parameters, pricing overrides and missing model status', async () => {
  await open(0, 5, {modelProvider: 'litellm', modelId: 'sagemaker/o1-reasoning', temperature: 1, maxTokens: 1200, topP: 0.9, thinkingBudget: 1000, reasoningEffort: 'high', maxCompletionTokens: 2048, openaiApiKey: 'fixture'});
  act(() => editor.onChange('bad')); send('s', {meta: true});
  await open(5,0, {modelId: ''}); act(() => editor.onSubmit('42'));
  expect(document.body.textContent).toContain('No model selected');
  await open(5,0, {modelPricing: {'claude-opus-4': {inputCostPer1k: 0, outputCostPer1k: 1}}});
  act(() => editor.onSubmit('0.02')); expect(config.modelPricing['claude-opus-4'].inputCostPer1k).toBe(0.02);
  await open(0,0, {modelProvider: 'unknown', modelId: undefined});
  send('', {escape: true}); send('x'); send('', {escape: true});
});
it('handles save failures, escape transitions and section expansion controls', async () => {
  jest.useFakeTimers(); saveConfig.mockRejectedValueOnce('readonly');
  await open(1,0); send('v', {ctrl: true}); send('', {escape: true}); send('x');
  send('s', {ctrl: true}); await act(async () => {await jest.advanceTimersByTimeAsync(350);});
  expect(document.body.textContent).toContain('Save failed: readonly');
  send('', {escape: true}); send('', {leftArrow: true}); send('', {rightArrow: true});
  send('', {escape: true}); send('', {escape: true});
  act(() => jest.advanceTimersByTime(6000));
});
it('validates bug bounty header maps and rejects non-object input', async () => {
  await open(1,8, {bugBountyHeaders: {'X-Test': 'researcher'}});
  act(() => editor.onSubmit('[]'));
  expect(updateConfig).not.toHaveBeenLastCalledWith({bugBountyHeaders: []});
  await open(1,8); act(() => editor.onSubmit('{"X-Test":"researcher"}'));
  expect(updateConfig).toHaveBeenLastCalledWith({bugBountyHeaders: {'X-Test': 'researcher'}});
  await open(1,8); act(() => editor.onSubmit(''));
  expect(updateConfig).toHaveBeenLastCalledWith({bugBountyHeaders: {}});
});
