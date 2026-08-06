import React from 'react';
import { TextEncoder, TextDecoder } from 'util';
import {describe, expect, it, jest} from '@jest/globals';
import fs from 'fs/promises';
import os from 'os';
import path from 'path';

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
  it('treats thinking events as non-rendering stream controls', async () => {
    const { EventLine, render } = await load();
    const startup = render(
      <EventLine
        event={{ type: 'thinking', context: 'startup', startTime: Date.now(), message: 'Initializing' } as any}
        animationsEnabled
      />
    ).lastFrame();
    const normal = render(
      <EventLine
        event={{ type: 'thinking', context: 'reasoning', message: 'Working' } as any}
        animationsEnabled
      />
    ).lastFrame();

    expect(startup || '').toBe('');
    expect(normal || '').toBe('');
  });

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
      { type: 'progress_update', step: 1, progressPercent: 40, totalTools: 4 },
      { type: 'progress_update', step: 'FINAL REPORT' },
      { type: 'progress_update', step: 'TERMINATED' },
      {type: 'progress_update', step: 1, progressPercent: 25},
      {
        type: 'progress_update',
        step: 1,
        progressPercent: 25,
        agent_name: 'web_tester',
        agent_sub_step: 2,
        agent_total_actions: 7
      },
      {
        type: 'progress_update',
        step: 'REPORT_AGENT',
        operation_stage: 'final_report',
        report_step_index: 2,
        report_step_total: 5,
        report_step_kind: 'validation_failure',
        report_step_label: 'Requires validation: SQL injection'
      },
      {
        type: 'progress_update',
        step: 'RAGAS_METRIC',
        operation_stage: 'ragas_evaluation',
        evaluation_step_index: 2,
        evaluation_step_total: 5,
        evaluation_step_label: 'Operation: Evidence Quality'
      },
      {
        type: 'progress_update',
        step: 'RAGAS_PREPARATION',
        operation_stage: 'ragas_evaluation',
        evaluation_step_kind: 'reference_topics',
        evaluation_step_label: 'Report: Generate Reference Topics'
      },
      { type: 'task_started', title: 'Enumerate target' },
      { type: 'task_started', title: 'Verify finding: IDOR', task_kind: 'finding_validation' },
      { type: 'thinking', context: 'reasoning', startTime: Date.now(), message: 'working' },
      { type: 'task_deferred', title: 'Enumerate routes', status: 'pending', status_reason: 'Phase budget cap reached' },
      { type: 'task_done', title: 'Enumerate target', status: 'blocked', status_reason: 'Needs credentials' },
      {
        type: 'task_done',
        title: 'Verify finding: IDOR',
        status: 'done',
        status_reason: 'Direct evidence approved',
        task_kind: 'finding_validation',
        finding_resolution: 'verified'
      },
      {
        type: 'task_done',
        title: 'Verify finding: SQL injection',
        status: 'partial_failure',
        status_reason: 'Control request missing',
        task_kind: 'finding_validation',
        finding_resolution: 'validation_failure'
      },
      { type: 'thinking_end' },
      { type: 'delayed_thinking_start' },
      { type: 'termination_reason', reason: 'complete', message: 'Assessment complete: 3 phases evaluated' },
      { type: 'termination_reason', reason: 'network_timeout', message: 'Network timeout. Switching to final report.' },
      { type: 'termination_reason', reason: 'max_tokens', message: 'Too many tokens' },
      { type: 'termination_reason', reason: 'rate_limited', message: 'Rate limited' },
      { type: 'termination_reason', reason: 'model_error', message: 'Model failed' },
      { type: 'termination_reason', reason: 'error', message: 'Workflow iteration limit reached' },
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
    expect(output).toContain('[PROGRESS 40% | 4 tools]');
    expect(output).toContain('[FINAL REPORT]');
    expect(output).toContain('[FINAL REPORT 2/5] [REQUIRES VALIDATION] Requires validation: SQL injection');
    expect(output).toContain('[RAGAS EVALUATION 2/5] Operation: Evidence Quality');
    expect(output).toContain('[RAGAS EVALUATION PREPARING] Report: Generate Reference Topics');
    expect(output).toContain('TASK BLOCKED Enumerate target: Needs credentials');
    expect(output).toContain('TASK DEFERRED Enumerate routes: Phase budget cap reached');
    expect(output).toContain('VERIFYING FINDING IDOR');
    expect(output).toContain('FINDING VERIFIED IDOR: Direct evidence approved');
    expect(output).toContain('FINDING REQUIRES VALIDATION SQL injection: Control request missing');
    expect(output).toContain('OPERATION COMPLETE');
    expect(output).toContain('Assessment complete: 3 phases evaluated');
    expect(output).toContain('NETWORK TIMEOUT');
    expect(output).toContain('TOKEN LIMIT');
    expect(output).toContain('TERMINATION ERROR');
    expect(output).toContain('Workflow iteration limit reached');
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
      { type: 'tool_start', tool_name: 'store_observation', tool_input: { content: 'observed endpoint', artifacts: [] } },
      { type: 'tool_start', tool_name: 'store_knowledge', tool_input: { content: 'reuse negative controls' } },
      { type: 'tool_start', tool_name: 'store_finding', tool_input: { title: 'SQLi', severity: 'HIGH', target: '/search' } },
      { type: 'tool_start', tool_name: 'record_finding_validation', tool_input: { finding_uid: 'abc', outcome: 'confirmed' } },
      { type: 'tool_start', tool_name: 'memory_get', tool_input: { query: 'finding' } },
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

  it('renders semantic evaluation failures and finalized scores without tool headers', async () => {
    const { EventLine, render } = await load();
    const failed = render(<EventLine event={{
      type: 'evaluation_step_complete',
      evaluation_scope: 'operation',
      evaluation_step_kind: 'metric',
      evaluation_metric: 'goal_accuracy',
      status: 'failed',
      message: 'Metric evaluation failed',
    } as any} animationsEnabled={false} />).lastFrame();
    const complete = render(<EventLine event={{
      type: 'evaluation_complete',
      status: 'completed',
      success: true,
      scores: {'operation/evidence_quality': 0.8, 'operation/goal_accuracy': 0.6},
      average_score: 0.7,
    } as any} animationsEnabled={false} />).lastFrame();
    const noResults = render(<EventLine event={{
      type: 'evaluation_complete',
      status: 'no_results',
      success: false,
      message: 'Evaluation produced no scores',
    } as any} animationsEnabled={false} />).lastFrame();
    const failure = render(<EventLine event={{
      type: 'evaluation_complete',
      status: 'failed',
      success: false,
      message: 'Evaluation failed; see logs for details',
    } as any} animationsEnabled={false} />).lastFrame();

    expect(failed).toContain('operation: goal accuracy failed');
    expect(failed).toContain('Metric evaluation failed');
    expect(complete).toContain('EVALUATION COMPLETE');
    expect(complete).toContain('Average: 70.0%');
    expect(complete).toContain('operation/evidence quality: 80.0%');
    expect(noResults).toContain('EVALUATION COMPLETED WITHOUT RESULTS');
    expect(failure).toContain('EVALUATION FAILED');
    expect(`${failed}\n${complete}\n${noResults}\n${failure}`).not.toContain('tool: evaluation');
  });

  it('renders agent names on tool_start headers', async () => {
    const {EventLine, render} = await load();
    const toolEvents: any[] = [
      {
        type: 'tool_start',
        tool_name: 'http_request',
        tool_input: {method: 'GET', url: 'https://example.com'},
        agent_name: 'web_tester',
      },
      {
        type: 'tool_start',
        tool_name: 'shell',
        tool_input: {command: 'whoami'},
        agent_name: 'recon_agent',
      },
      {
        type: 'tool_start',
        tool_name: 'custom_tool',
        tool_input: {alpha: 1},
        agent_name: 'custom_agent',
      },
    ];

    const output = toolEvents
      .map(event => render(<EventLine event={event} animationsEnabled={false}/>).lastFrame())
      .join('\n');

    expect(output).toContain('tool: http_request (web_tester)');
    expect(output).toContain('tool: shell (recon_agent)');
    expect(output).toContain('tool: custom_tool (custom_agent)');
  });

  it('groups stream events, renders batch/static streams, and handles output variants', async () => {
    const { computeDisplayGroups, StreamDisplay, StaticStreamDisplay, EventLine, render } = await load();
    const events: any[] = [
      { type: 'operation_init', operation_id: 'op2', target: 'example.com' },
      { type: 'progress_update', step: 2, progressPercent: 50 },
      { type: 'tool_start', tool_name: 'shell', tool_input: { command: 'whoami' } },
      { type: 'output', content: 'root', metadata: { fromToolBuffer: true, tool: 'shell' } },
      { type: 'tool_output', tool: 'shell', status: 'success', output: { stdout: 'ok' } },
      { type: 'report_content', content: '# Report\nFinding' },
      { type: 'batch', id: 'batch-1', events: [{ type: 'output', content: 'batched output' }] },
      { type: 'specialist_start', specialist: 'auth', task: 'test login', finding: 'weak session', artifactPaths: ['/tmp/a'] },
      { type: 'specialist_progress', specialist: 'auth', gate: 1, totalGates: 3, tool: 'browser', status: 'running' },
      { type: 'specialist_end', specialist: 'auth', result: { status: 'done', summary: 'ok' } },
    ];

    expect(computeDisplayGroups(events).length).toBeGreaterThan(0);
    const streamFrame = render(<StreamDisplay events={events} animationsEnabled={false} />).lastFrame();
    expect(streamFrame).toContain('Operation initialization complete');
    expect(streamFrame).toContain('ok');
    const staticFrame = render(<StaticStreamDisplay events={events} terminalWidth={100} availableHeight={40} />).lastFrame();
    expect(staticFrame).toContain('whoami');
    expect(staticFrame).toContain('ok');

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

  it('renders workflow activity lifecycle without an inline spinner', async () => {
    const { StreamDisplay, render } = await load();
    const started = {
      type: 'workflow_activity',
      role: 'task_creator',
      action: 'task_create_prompt',
      status: 'started',
      attempt: 1,
      attempt_total: 2,
      cycle: 1,
      cycle_total: 3,
    };
    const view = render(<StreamDisplay events={[started] as any} animationsEnabled={false} />);

    expect(view.lastFrame()).not.toContain('spinner:dots');
    expect(view.lastFrame()).toContain('started');
    expect(view.lastFrame()).toContain('[cycle 1/3]');

    view.rerender(<StreamDisplay events={[started, {
      ...started,
      status: 'completed',
    }] as any} animationsEnabled={false} />);
    const completedFrame = view.lastFrame() || '';
    expect(completedFrame).not.toContain('spinner:dots');
    expect(completedFrame).toContain('completed');
  });

  it('includes termination reason and message in the terminated progress header', async () => {
    const { StreamDisplay, render } = await load();
    const frame = render(<StreamDisplay events={[
      { type: 'progress_update', step: 'TERMINATED' },
      {
        type: 'termination_reason',
        reason: 'stalled',
        message: 'Task executor stopped after bounded recovery',
      },
    ] as any} animationsEnabled={false} />).lastFrame() || '';

    expect(frame).toContain('[TERMINATED: stalled] Task executor stopped after bounded recovery');
  });

  it('renders long static history through append-style output', async () => {
    const { StaticStreamDisplay, render } = await load();
    const events = Array.from({ length: 540 }, (_, index) => ({
      type: 'output',
      content: `line-${index}`,
    }));

    const output = render(
      <StaticStreamDisplay events={events as any} terminalWidth={100} availableHeight={40} />
    ).lastFrame();

    expect(output).toContain('line-0');
    expect(output).toContain('line-539');
  });

  it('updates completed stream content after an initial tool output', async () => {
    const { StaticStreamDisplay, render } = await load();
    const firstEvents: any[] = [
      { type: 'progress_update', step: 3, id: 'progress-3' },
      { type: 'tool_start', tool_name: 'shell', tool_id: 'tool-which', tool_input: { command: 'which feroxbuster' }, id: 'tool-which' },
      { type: 'output', content: '/usr/local/bin/feroxbuster', metadata: { fromToolBuffer: true, tool: 'shell' }, id: 'which-output' },
    ];
    const nextEvents: any[] = [
      ...firstEvents,
      { type: 'reasoning', content: 'Need to enumerate directories.', id: 'reasoning-next' },
      { type: 'progress_update', step: 4, id: 'progress-4' },
      {
        type: 'tool_start',
        tool_name: 'shell',
        tool_id: 'tool-ferox',
        tool_input: { command: 'feroxbuster -u http://host.docker.internal:32782' },
        id: 'tool-ferox',
      },
    ];

    const view = render(<StaticStreamDisplay events={firstEvents} terminalWidth={100} availableHeight={40} />);
    expect(view.lastFrame()).toContain('/usr/local/bin/feroxbuster');

    view.rerender(<StaticStreamDisplay events={nextEvents} terminalWidth={100} availableHeight={40} />);
    const updated = view.lastFrame();
    expect(updated).toContain('/usr/local/bin/feroxbuster');
    expect(updated).toContain('Need to enumerate directories.');
    expect(updated).toContain('feroxbuster -u http://host.docker.internal:32782');
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

  it('reads bounded report previews from disk without loading entire large reports', async () => {
    const { readReportPreviewFile } = await load();
    const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'cyber-report-preview-'));
    try {
      const smallPath = path.join(tmpDir, 'small.md');
      await fs.writeFile(smallPath, '# Short report\nbody', 'utf-8');
      await expect(readReportPreviewFile(smallPath, 1024)).resolves.toBe('# Short report\nbody');

      const largePath = path.join(tmpDir, 'large.md');
      const head = 'A'.repeat(3000);
      const middle = 'M'.repeat(6000);
      const tail = 'Z'.repeat(3000);
      await fs.writeFile(largePath, `${head}${middle}${tail}`, 'utf-8');

      const preview = await readReportPreviewFile(largePath, 2048);

      expect(preview.length).toBeLessThan(2400);
      expect(preview).toContain('AAA');
      expect(preview).toContain('ZZZ');
      expect(preview).toContain('report file preview truncated');
      expect(preview).not.toContain(middle);
    } finally {
      await fs.rm(tmpDir, {recursive: true, force: true});
    }
  });
});
