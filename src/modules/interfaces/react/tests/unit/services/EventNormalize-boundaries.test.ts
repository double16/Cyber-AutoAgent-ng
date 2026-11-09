import {expect, it} from '@jest/globals';
import {normalizeEvent} from '../../../src/services/events/normalize.js';
const start = (tool_name: string, tool_input?: any, extra = {}) => normalizeEvent({type: 'tool_start', tool_name, tool_input, ...extra});
it('normalizes legacy shell inputs without mutating callers', () => {
  for (const raw of [undefined, null, 3, {command: 3}, {cmd: {command: 'ls'}}, {commands: '[{"command":"ls"}]'}, {input: '{"command":"ls"}'}, {command: '[bad]'}, {command: '[\\n"ls"\\n]'}, ['ls']]) {
    const result = start('shell', raw);
    expect(result.tool_input).toBeDefined(); expect(result.toolId).toMatch(/^shell-/);
  }
  expect(start('shell', {command: 3}).tool_input.command).toEqual(['3']);
  expect(start('shell', {command: '[\\n"ls"\\n]'}).tool_input.command).toEqual(['ls']);
});
it('handles empty and structured tool inputs with absent optional fields', () => {
  expect(start('file_write', {}).tool_input).toEqual({path: undefined, content: undefined});
  expect(start('http_request', {}).tool_input).toEqual({method: 'GET', url: ''});
  expect(start('editor', {file_text: '', path: 3, command: 4}).tool_input).toMatchObject({path: '3', command: '4', file_text_length: 0});
  expect(start('editor', {}).tool_input).toEqual({});
  expect(start('python_repl', {code: 'short'}).tool_input).toEqual({code: 'short'});
  expect(start('other', '{bad}').tool_input).toBe('{bad}');
  expect(start('other', 'plain').tool_input).toBe('plain');
  expect(start('other', {nested: [null, false, 3, {data: 'x'.repeat(33000)}]}).tool_input.nested[3].data).toContain('truncated');
  expect(start('', {}, {timestamp: 'invalid'}).toolId).toMatch(/^tool-/);
});
it('normalizes sparse specialist, report, command, output and prompt events', () => {
  expect(normalizeEvent(null as any)).toBeNull();
  expect(normalizeEvent('raw' as any)).toBe('raw');
  for (const type of ['preflight_check', 'specialist_start', 'specialist_progress', 'specialist_end', 'report_paths', 'prompt_change']) {
    expect(normalizeEvent({type}).type).toBe(type);
  }
  expect(normalizeEvent({type: 'command', content: '{bad}'}).content).toBe('{bad}');
  expect(normalizeEvent({type: 'command', content: '{}'}).content).toBe('{}');
  expect(normalizeEvent({type: 'tool_output', output: {}}).output).toEqual({});
  expect(normalizeEvent({type: 'tool_output', output: {stdout: 'out', stderr: 'err'}}).output.text).toBe('out\nerr');
  expect(normalizeEvent({type: 'tool_output', output: {stdout: 'out'}}).output.text).toBe('out');
  expect(normalizeEvent({type: 'tool_output', output: {stderr: 'err'}}).output.text).toBe('err');
  expect(normalizeEvent({type: 'tool_output', status: 'error'}).status).toBe('error');
  expect(normalizeEvent({type: 'prompt_change', overlay: 'bad', directives: [3], summary: 3, note: 4})).toMatchObject({overlay: undefined, directives: ['3'], summary: '3', note: '4'});
  expect(normalizeEvent({type: 'unknown', timestamp: NaN, metadata: {value: null, flag: true}}).metadata).toEqual({value: null, flag: true});
});
