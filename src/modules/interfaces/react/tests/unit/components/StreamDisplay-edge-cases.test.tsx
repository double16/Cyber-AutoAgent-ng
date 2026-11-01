import React from 'react';
import {afterEach, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const config = {outputDir: '/tmp/coverage-output'};
jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({useConfig: () => ({config})}));
jest.unstable_mockModule('ink-spinner', () => ({default: () => <span>spinner</span>}));
const {EventLine, StreamDisplay, StaticStreamDisplay} = await import('../../../src/components/StreamDisplay.js');
let view: ReturnType<typeof TestRenderer.create> | undefined;
const render = (event: any, props = {}) => {
  act(() => {if (view) view.update(<EventLine event={event} animationsEnabled={false} {...props}/>); else view = TestRenderer.create(<EventLine event={event} animationsEnabled={false} {...props}/>);});
  return document.body.textContent || '';
};
afterEach(() => {if (view) act(() => view!.unmount()); view = undefined; delete process.env.CYBER_MAX_GROUPS_RENDERED;});
it('renders sparse tool contracts and supplied streamed inputs without losing headers', () => {
  for (const tool_name of ['swarm', 'memory_get', 'memory_retrieve', 'memory_list', 'shell', 'http_request', 'browser_goto_url', 'browser_perform_action', 'browser_observe_page', 'browser_evaluate_js', 'browser_get_cookies', 'browser_get_page_html', 'browser_take_screenshot', 'browser_set_headers', 'file_write', 'editor', 'think', 'python_repl', 'report_generator', 'handoff_to_agent', 'load_tool', 'unknown']) {
    expect(render({type: 'tool_start', tool_name})).toContain(tool_name);
  }
  const inputs = [null, '', 'text', [], ['a'], {}, {x: 1}, true];
  for (const input of inputs) {
    expect(render({type: 'tool_start', tool_name: 'unknown', tool_id: 'tool', tool_input: input}, {toolInputs: new Map([['tool', input]])})).toContain('unknown');
  }
  expect(render({type: 'tool_start', tool_name: 'python_repl', tool_id: 'py', tool_input: {}}, {toolInputs: new Map([['py', {code: 'print(1)'}]])})).toContain('print(1)');
  for (const code of [3, {source: 'code'}, '']) expect(render({type: 'tool_start', tool_name: 'python_repl', tool_input: {code}})).toContain('python_repl');
});
it('renders legacy shell command shapes and optional command execution details', () => {
  for (const command of [[null, {cmd: 'id'}, {value: 'pwd'}, {args: ['echo', 'hello']}, {custom: true}, 7], '{"command":"ls"}', '["ls","pwd"]', '[bad', {args: ['whoami']}, '']) {
    expect(render({type: 'tool_start', tool_name: 'shell', tool_input: {command, timeout: 10, parallel: true}})).toContain('parallel execution');
  }
  for (const query of [3, {nested: 'data'}, 'x'.repeat(90)]) expect(render({type: 'tool_start', tool_name: 'memory_get', tool_input: {query}})).toContain('query:');
});
it('formats bounded tool, report and operation output while suppressing internal log noise', () => {
  const lines = Array.from({length: 2000}, (_, i) => `row ${i}`).join('\n');
  expect(render({type: 'output', content: 'raw\nraw\nlast'})).toContain('last');
  for (const content of ['▶ Startup', '◆ ready', '◆ starting', '✓ success', '○ warning', '[Observability] ready', {key: true}, 'x'.repeat(40000), lines]) {
    expect(render({type: 'output', content})).not.toBe('');
  }
  expect(render({type: 'output', content: '# SECURITY ASSESSMENT REPORT\n'+lines})).toContain('FINAL REPORT');
  expect(render({type: 'output', content: 'Outputs stored in: /tmp\nHost: /tmp\nContainer: /app\n'+lines})).toContain('OPERATION COMPLETE');
  expect(render({type: 'output', content: 'x'.repeat(40000), metadata: {fromToolBuffer: true, tool: 'python_repl'}})).toContain('content continues');
  expect(render({type: 'output', content: lines, metadata: {fromToolBuffer: true}})).toContain('content continues');
  for (const fromToolBuffer of [true, false]) {
    expect(render({type: 'output', content: '__CYBER_EVENT__secret\n2026-01-01 12:00:00 - INFO - log\nError parsing event: failed', metadata: {fromToolBuffer}})).toBe('');
  }
  expect(render({type: 'output', content: 'Event loop cycle stop requested\nCode executed successfully\nError:\noutput [2 lines]'})).toBe('');
  expect(render({type: 'output', content: null})).toBe('');
});
it('renders tool output fallbacks, specialist outcomes, errors and metadata safely', () => {
  expect(render({type: 'tool_output'})).toBe('');
  expect(render({type: 'tool_output', output: null})).toBe('');
  for (const output of ['text', {text: 'text'}, {stdout: 'out'}, {stderr: 'err'}, ['a','b'], {custom: true}]) {
    expect(render({type: 'tool_output', output, status: 'error', toolName: 'scanner'})).toContain('scanner: error');
  }
  for (const event of [{type: 'error'}, {type: 'metadata'}, {type: 'metadata', content: {}}, {type: 'metadata', content: {stopping: true}}, {type: 'specialist_start'}, {type: 'specialist_progress'}, {type: 'specialist_progress', tool: 'scan'}, {type: 'specialist_end'}, {type: 'operation_terminated'}, {type: 'operation_finalized'}, {type: 'evaluation_complete'}, {type: 'evaluation_step_complete', status: 'skipped'}]) render(event);
  for (const status of ['hypothesis', 'unverified', 'error']) expect(render({type: 'specialist_end', result: {validation_status: status}})).toContain(status);
  const cycle: any = {}; cycle.self = cycle;
  expect(render({type: 'metadata', content: {empty: null, object: {a: 'x'.repeat(70)}, circular: cycle, long: 'x'.repeat(70)}})).toContain('[object]');
});
it('retains final report groups, combines streamed reasoning, and processes input corrections', () => {
  process.env.CYBER_MAX_GROUPS_RENDERED = '51';
  const events: any[] = [
    {type: 'reasoning', content: 'first', agent_name: 'worker'}, {type: 'reasoning_delta', delta: 'second'}, {type: 'reasoning', content: ' '}, {type: 'reasoning_delta'},
    {type: 'tool_start', tool_id: 'tool', tool_name: 'shell', tool_input: ''}, {type: 'tool_input_corrected', tool_id: 'tool', tool_input: {command: 'pwd'}},
    ...Array.from({length: 60}, (_, i) => ({type: 'output', content: `row ${i}`, timestamp: i})),
  ];
  act(() => {view = TestRenderer.create(<StreamDisplay events={events} animationsEnabled={false}/>);});
  expect(document.body.textContent).toContain('earlier items omitted');
  const reportEvents = [{type: 'progress_update', step: 'FINAL REPORT'}, ...events] as any;
  act(() => view!.update(<StreamDisplay events={reportEvents} animationsEnabled={false}/>));
  expect(document.body.textContent).not.toContain('earlier items omitted');
  act(() => view!.update(<StaticStreamDisplay events={events} terminalWidth={80} availableHeight={24}/>));
  expect(document.body.textContent).toContain('first second');
  act(() => view!.update(<StaticStreamDisplay events={[...events]} terminalWidth={70}/>));
  expect(document.body.textContent).toContain('row 59');
});

it('renders absent initialization metadata, memory summaries and provider-specific termination guidance', () => {
  expect(render({type: 'operation_init'})).not.toContain('Operation ID:');
  expect(render({type: 'operation_init', operation_id: '', target: '', objective: '', model_id: '', provider: '', memory: {}, ui_mode: '', observability: false, tools_available: 0})).toContain('Observability: disabled');
  expect(render({type: 'operation_init', memory: {backend: '', has_existing: false, total_count: 0, categories: {}, recent_findings: []}})).toContain('Memory backend: unknown');
  for (const reason of ['budget_limit', 'user_abort', 'network_error', 'max_tokens', 'unknown']) {
    expect(render({type: 'termination_reason', reason, message: ''})).not.toBe('');
  }
  for (const configOverride of [{}, {modelProvider: 'ollama', ollamaHost: 'http://local'}, {modelProvider: 'custom_provider', awsRegion: 'region'}]) {
    expect(render({type: 'termination_reason', reason: 'timeout', message: 'Switching to final report.'}, {configOverride})).toContain('NETWORK TIMEOUT');
  }
  expect(render({type: 'task_started'})).toBe('');
  expect(render({type: 'task_started', task_kind: 'finding_validation'})).toContain('Finding');
  expect(render({type: 'task_done', finding_resolution: 'validation_failure'})).not.toBe('');
  expect(render({type: 'task_deferred'})).not.toBe('');
});
