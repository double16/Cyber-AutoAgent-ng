import React from 'react';
import { TextEncoder, TextDecoder } from 'util';
import { jest } from '@jest/globals';

if (typeof global.TextEncoder === 'undefined') {
  global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
  global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

jest.unstable_mockModule('ink-spinner', () => ({
  default: ({ type }: { type?: string }) => <span>spinner:{type}</span>,
}));

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
  useConfig: () => ({
    config: {
      modelProvider: 'bedrock',
      awsRegion: 'us-east-1',
      outputDir: './outputs',
    },
  }),
}));

const load = async () => {
  const mod = await import('../../../src/components/StreamDisplay.js');
  const { render } = await import('ink-testing-library');
  return { ...mod, render };
};

describe('StreamDisplay broad event rendering', () => {
  it('renders SDK, lifecycle, reasoning, termination, and metadata event variants', async () => {
    const { EventLine, render } = await load();
    const events: any[] = [
      { type: 'model_invocation_start', modelId: 'claude' },
      { type: 'model_stream_delta', delta: 'token' },
      { type: 'reasoning_delta', delta: 'hidden' },
      { type: 'tool_invocation_start', toolName: 'ignored' },
      { type: 'tool_invocation_end', success: true, duration: 12 },
      { type: 'event_loop_cycle_start', cycleNumber: 3 },
      { type: 'metrics_update', metrics: { tokens: 1 } },
      { type: 'content_block_delta', delta: 'visible', isReasoning: false },
      { type: 'content_block_delta', delta: 'think', isReasoning: true },
      { type: 'step_header', step: 2, maxSteps: 5, totalTools: 4 },
      { type: 'step_header', step: 'FINAL REPORT', maxSteps: 5 },
      { type: 'step_header', step: 'TERMINATED' },
      { type: 'step_header', step: 1, maxSteps: 3, is_swarm_operation: true },
      { type: 'step_header', step: 1, maxSteps: 3, swarm_agent: 'web_tester', swarm_sub_step: 2, swarm_total_iterations: 7 },
      { type: 'task_started', title: 'Enumerate target' },
      { type: 'thinking', context: 'reasoning', startTime: Date.now(), message: 'working' },
      { type: 'task_done', title: 'Enumerate target' },
      { type: 'thinking_end' },
      { type: 'delayed_thinking_start' },
      { type: 'termination_reason', reason: 'network_timeout', message: 'Network timeout. Switching to final report.' },
      { type: 'termination_reason', reason: 'max_tokens', message: 'Too many tokens' },
      { type: 'termination_reason', reason: 'rate_limited', message: 'Rate limited' },
      { type: 'termination_reason', reason: 'model_error', message: 'Model failed' },
      { type: 'termination_reason', reason: 'swarm_iteration_limit', message: 'swarm iteration limit' },
      { type: 'reasoning', content: 'I should inspect headers and forms' },
      { type: 'command', content: 'python scan.py' },
      { type: 'command', command: ['python', '-m', 'scanner'], content: '' },
      { type: 'error', content: 'failed hard' },
      { type: 'metadata', content: { target: 'example.com', module: 'web' } },
      { type: 'divider' },
      { type: 'separator', content: 'phase break' },
      { type: 'user_handoff', message: 'Need OTP', breakout: true },
      { type: 'operation_init', operation_id: 'op1', target: 'https://example.com', objective: 'audit', memory: { enabled: true } },
      { type: 'report_paths', operation_id: 'op1', target: 'example.com', reportPath: '/app/outputs/example.com/op1/report.md' },
    ];

    const output = events.map(event => render(<EventLine event={event} animationsEnabled={false} />).lastFrame()).join('\n');

    expect(output).toContain('model invocation started');
    expect(output).toContain('Event loop cycle started');
    expect(output).toContain('[STEP 2/5 | 4 tools]');
    expect(output).toContain('[FINAL REPORT]');
    expect(output).toContain('NETWORK TIMEOUT');
    expect(output).toContain('TOKEN LIMIT');
    expect(output).toContain('I should inspect');
    expect(output).toContain('python scan.py');
    expect(output).toContain('failed hard');
    expect(output).toContain('Need OTP');
    expect(output).toContain('Operation initialization complete');
    expect(output).toContain('report.md');
  });

  it('renders common tool_start variants without throwing', async () => {
    const { EventLine, render } = await load();
    const toolEvents: any[] = [
      { type: 'tool_start', tool_name: 'swarm', tool_input: { task: 'coordinate agents', agents: ['recon', 'web'] } },
      { type: 'tool_start', tool_name: 'mem0_store', tool_input: { content: 'remember finding' } },
      { type: 'tool_start', tool_name: 'mem0_get', tool_input: { query: 'finding' } },
      { type: 'tool_start', tool_name: 'shell', tool_input: { command: 'nmap -sV example.com' } },
      { type: 'tool_start', tool_name: 'http_request', tool_input: { method: 'GET', url: 'https://example.com' } },
      { type: 'tool_start', tool_name: 'browser_goto_url', tool_input: { url: 'https://example.com/login' } },
      { type: 'tool_start', tool_name: 'browser_perform_action', tool_input: { action: 'click', selector: '#login' } },
      { type: 'tool_start', tool_name: 'browser_observe_page', tool_input: { observation_goal: 'find forms' } },
      { type: 'tool_start', tool_name: 'browser_evaluate_js', tool_input: { script: 'document.title' } },
      { type: 'tool_start', tool_name: 'browser_get_cookies', tool_input: {} },
      { type: 'tool_start', tool_name: 'browser_get_page_html', tool_input: {} },
      { type: 'tool_start', tool_name: 'browser_set_headers', tool_input: { headers: { Authorization: 'Bearer x' } } },
      { type: 'tool_start', tool_name: 'file_write', tool_input: { path: '/tmp/a.txt', content: 'hello' } },
      { type: 'tool_start', tool_name: 'editor', tool_input: { command: 'replace', path: 'app.py' } },
      { type: 'tool_start', tool_name: 'think', tool_input: { thought: 'Need another payload' } },
      { type: 'tool_start', tool_name: 'python_repl', tool_input: { code: 'print(1)' } },
      { type: 'tool_start', tool_name: 'report_generator', tool_input: { title: 'Assessment report' } },
      { type: 'tool_start', tool_name: 'handoff_to_agent', tool_input: { agent: 'web', task: 'test auth' } },
      { type: 'tool_start', tool_name: 'load_tool', tool_input: { tool_name: 'dns_lookup' } },
      { type: 'tool_start', tool_name: 'stop', tool_input: { reason: 'done' } },
      { type: 'tool_start', tool_name: 'unknown_tool', tool_input: { alpha: 1, beta: 'two' } },
    ];

    const output = toolEvents.map(event => render(<EventLine event={event} animationsEnabled={false} />).lastFrame()).join('\n');

    expect(output).toContain('tool: swarm');
    expect(output).toContain('tool: shell');
    expect(output).toContain('nmap -sV');
    expect(output).toContain('https://example.com');
    expect(output).toContain('tool: report_generator');
    expect(output).toContain('unknown_tool');
  });

  it('groups stream events, renders batch/static streams, and handles output variants', async () => {
    const { computeDisplayGroups, StreamDisplay, StaticStreamDisplay, EventLine, render } = await load();
    const events: any[] = [
      { type: 'operation_init', operation_id: 'op2', target: 'example.com' },
      { type: 'step_header', step: 1, maxSteps: 2 },
      { type: 'tool_start', tool_name: 'shell', tool_input: { command: 'whoami' } },
      { type: 'output', content: 'root', metadata: { fromToolBuffer: true, tool: 'shell' } },
      { type: 'tool_output', tool: 'shell', status: 'success', output: { stdout: 'ok' } },
      { type: 'report_content', content: '# Report\nFinding' },
      { type: 'batch', id: 'batch-1', events: [{ type: 'output', content: 'batched output' }] },
      { type: 'swarm_start', task: 'audit', agent_names: ['recon', 'web'] },
      { type: 'swarm_handoff', from_agent: 'recon', to_agent: 'web', message: 'handoff' },
      { type: 'swarm_complete', final_agent: 'web', execution_count: 2 },
      { type: 'specialist_start', specialist: 'auth', task: 'test login', finding: 'weak session', artifactPaths: ['/tmp/a'] },
      { type: 'specialist_progress', specialist: 'auth', gate: 1, totalGates: 3, tool: 'browser', status: 'running' },
      { type: 'specialist_end', specialist: 'auth', result: { status: 'done', summary: 'ok' } },
    ];

    expect(computeDisplayGroups(events).length).toBeGreaterThan(0);
    expect(render(<StreamDisplay events={events} animationsEnabled={false} />).lastFrame()).toContain('Operation initialization complete');
    expect(render(<StaticStreamDisplay events={events} terminalWidth={100} availableHeight={40} />).lastFrame()).toContain('whoami');

    const longOutput = render(
      <EventLine
        event={{
          type: 'output',
          content: '\u001b[31m' + Array.from({ length: 50 }, (_, index) => `line ${index}`).join('\n'),
          exitCode: 1,
          duration: 1234,
        } as any}
        animationsEnabled={false}
      />
    ).lastFrame();
    expect(longOutput).toContain('line 0');
    expect(longOutput).toContain('line 49');
  });

  it('resolves report path candidates across absolute, relative, inferred, and unsafe inputs', async () => {
    const { mapContainerReportPath, getReportPathCandidates } = await load();

    expect(mapContainerReportPath('/app/outputs/example/op/report.md', '/tmp/out'))
      .toBe('/tmp/out/example/op/report.md');
    expect(mapContainerReportPath('/app/outputs/example/op/report.md', null))
      .toBe('/app/outputs/example/op/report.md');

    const relative = getReportPathCandidates(
      { operationId: 'op-3', target: 'https://../target.example/a?b=1' },
      'reports/final.md',
      '/repo',
      '/tmp/out'
    );
    expect(relative).toContain('/tmp/out/reports/final.md');
    expect(relative).toContain('/repo/reports/final.md');
    expect(relative.some(path => path.includes('unknown_target'))).toBe(false);
    expect(relative.some(path => path.includes('target.example'))).toBe(true);

    const absolute = getReportPathCandidates(
      { operationId: null, target: null },
      '/var/reports/final.md',
      null,
      undefined
    );
    expect(absolute[0]).toBe('/var/reports/final.md');

    expect(getReportPathCandidates({}, null, null, null)).toEqual([]);
  });
});
