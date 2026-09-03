import { describe, expect, it, jest } from '@jest/globals';
import { DirectDockerService } from '../../../src/services/DirectDockerService.js';
import { PythonExecutionService } from '../../../src/services/PythonExecutionService.js';

function wrapEvent(obj: any): string {
  return `__CYBER_EVENT__${JSON.stringify(obj)}__CYBER_EVENT_END__`;
}

function captureEvents(service: any): any[] {
  const emitted: any[] = [];
  service.on('event', (event: any) => emitted.push(event));
  return emitted;
}

describe('shared cyber event stream parsing behavior', () => {
  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents', 'python_repl'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream', 'shell'],
  ])('%s buffers raw tool stdout before structured events', (_name, createService, method, toolName) => {
    const service: any = createService();
    const emitted = captureEvents(service);

    service[method](wrapEvent({ type: 'tool_start', tool_name: toolName, timestamp: 1 }));
    service[method](`raw output\n${wrapEvent({ type: 'progress_update', content: 'next', timestamp: 2 })}`);

    const chunks = emitted.filter(event => event?.metadata?.fromToolBuffer);
    expect(chunks).toHaveLength(1);
    expect(chunks[0].content).toBe('raw output\n');
    expect(chunks[0].metadata.tool).toBe(toolName);
  });

  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream'],
  ])('%s marks synthetic tool_start output for UI suppression', (_name, createService, method) => {
    const service: any = createService();
    const emitted = captureEvents(service);

    service[method](wrapEvent({ type: 'tool_start', tool_name: 'shell', tool_input: { command: 'id' }, timestamp: 1 }));

    expect(emitted).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: 'output',
        metadata: expect.objectContaining({ syntheticToolStart: true }),
      }),
      expect.objectContaining({
        type: 'tool_start',
        tool_name: 'shell',
      }),
    ]));
  });

  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents', 'python_repl'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream', 'shell'],
  ])('%s does not flush buffered raw output again after backend tool output', (_name, createService, method, toolName) => {
    const service: any = createService();
    const emitted = captureEvents(service);

    service[method](wrapEvent({ type: 'tool_start', tool_name: toolName, timestamp: 1 }));
    service[method]('partial raw output');
    service[method](wrapEvent({
      type: 'output',
      content: 'backend tool output',
      metadata: { fromToolBuffer: true },
      timestamp: 2,
    }));
    service[method](wrapEvent({ type: 'tool_end', tool_name: toolName, success: true, timestamp: 3 }));

    const chunks = emitted.filter(event => event?.metadata?.fromToolBuffer);
    expect(chunks.map(event => event.content)).toEqual(['backend tool output']);
  });

  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream'],
  ])('%s distinguishes blocked and executed tool failures', (_name, createService, method) => {
    const service: any = createService();
    const emitted = captureEvents(service);

    service[method](wrapEvent({
      type: 'tool_end',
      tool_name: 'shell',
      success: false,
      outcome: 'blocked',
      executed: false,
      timestamp: 1,
    }));
    service[method](wrapEvent({
      type: 'tool_end',
      tool_name: 'shell',
      success: false,
      outcome: 'error',
      executed: true,
      timestamp: 2,
    }));

    expect(emitted).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'output', content: '🚫 shell (blocked)' }),
      expect.objectContaining({ type: 'output', content: '❌ shell (failed)' }),
    ]));
  });

  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream'],
  ])('%s renders a lifecycle-only failure summary without duplicating backend output', (_name, createService, method) => {
    const service: any = createService();
    const emitted = captureEvents(service);

    service[method](wrapEvent({ type: 'tool_start', tool_name: 'record_task_acceptance', timestamp: 1 }));
    service[method](wrapEvent({
      type: 'tool_end',
      tool_name: 'record_task_acceptance',
      success: false,
      executed: true,
      error_summary: 'Missing execution evidence',
      timestamp: 2,
    }));

    expect(emitted).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: 'output',
        content: '❌ record_task_acceptance (failed)\nMissing execution evidence',
      }),
    ]));

    emitted.length = 0;
    service[method](wrapEvent({ type: 'tool_start', tool_name: 'record_task_acceptance', timestamp: 3 }));
    service[method](wrapEvent({
      type: 'output',
      content: 'Missing execution evidence',
      metadata: { fromToolBuffer: true },
      timestamp: 4,
    }));
    service[method](wrapEvent({
      type: 'tool_invocation_end',
      tool_name: 'record_task_acceptance',
      success: false,
      timestamp: 5,
    }));
    service[method](wrapEvent({
      type: 'tool_end',
      tool_name: 'record_task_acceptance',
      success: false,
      executed: true,
      error_summary: 'Missing execution evidence',
      timestamp: 6,
    }));

    const failureOutput = emitted.find(event => event.type === 'output' && event.content.startsWith('❌'));
    expect(failureOutput.content).toBe('❌ record_task_acceptance (failed)');
  });

  it('emits a user-visible parse error for malformed Docker events', () => {
    const service: any = new DirectDockerService();
    const emitted = captureEvents(service);

    service.parseEvents('__CYBER_EVENT__{"type":__CYBER_EVENT_END__');

    expect(emitted).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: 'output',
        content: expect.stringContaining('Error parsing event:'),
      }),
    ]));
  });

  it('logs and skips malformed Python events without emitting them', () => {
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const service: any = new PythonExecutionService();
    const emitted = captureEvents(service);

    try {
      service.processOutputStream('__CYBER_EVENT__{"type":__CYBER_EVENT_END__');
      service.processOutputStream(wrapEvent({ type: 'output', content: 'after malformed', timestamp: 2 }));

      expect(emitted).toEqual([
        expect.objectContaining({ type: 'output', content: 'after malformed' }),
      ]);
    } finally {
      warnSpy.mockRestore();
    }
  });

  it('preserves ANSI escapes while removing Docker control characters from tool stdout', () => {
    const service: any = new DirectDockerService();
    const emitted = captureEvents(service);

    service.parseEvents(wrapEvent({ type: 'tool_start', tool_name: 'python_repl', timestamp: 1 }));
    service.parseEvents('\x00\x01\x1b[31mred\x1b[0m\x7f');
    service.parseEvents(wrapEvent({ type: 'tool_end', tool_name: 'python_repl', success: true, timestamp: 2 }));

    const chunks = emitted.filter(event => event?.metadata?.fromToolBuffer);
    expect(chunks.map(event => event.content).join('')).toBe('\x1b[31mred\x1b[0m');
  });

  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream'],
  ])('%s passes tool-discovery status events through without synthetic output', (_name, createService, method) => {
    const service: any = createService();
    const emitted = captureEvents(service);

    service[method](wrapEvent({ type: 'tool_discovery_start', timestamp: 1 }));
    service[method](wrapEvent({ type: 'tool_available', tool_name: 'scanner', description: 'Scan hosts', timestamp: 2 }));
    service[method](wrapEvent({ type: 'tool_unavailable', tool_name: 'browser', timestamp: 3 }));
    service[method](wrapEvent({ type: 'environment_ready', tool_count: 2, timestamp: 4 }));

    expect(emitted).toHaveLength(4);
    expect(emitted).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'tool_discovery_start', data: {}, metadata: {}, sessionId: '' }),
      expect.objectContaining({ type: 'tool_available', tool_name: 'scanner', description: 'Scan hosts' }),
      expect.objectContaining({ type: 'tool_unavailable', tool_name: 'browser' }),
      expect.objectContaining({ type: 'environment_ready', tool_count: 2 }),
    ]));
    expect(emitted.some(event => event.type === 'output')).toBe(false);
  });

  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream'],
  ])('%s replays only discovery events emitted before the terminal attaches', (_name, createService, method) => {
    const service: any = createService();

    service[method](wrapEvent({ type: 'tool_discovery_start', timestamp: 1 }));
    expect(service.drainBufferedStartupEvents()).toEqual([
      expect.objectContaining({ type: 'tool_discovery_start' }),
    ]);

    service.markStartupEventConsumerAttached();
    service[method](wrapEvent({ type: 'environment_ready', tool_count: 1, timestamp: 2 }));
    expect(service.drainBufferedStartupEvents()).toEqual([]);
  });

  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream'],
  ])('%s emits completion for operation_finalized events', (_name, createService, method) => {
    const service: any = createService();
    const emitted = captureEvents(service);
    let completeCount = 0;
    service.on('complete', () => completeCount += 1);

    service[method](wrapEvent({ type: 'operation_terminated', timestamp: 6 }));
    expect(completeCount).toBe(0);

    service[method](wrapEvent({ type: 'operation_finalized', timestamp: 7 }));

    expect(completeCount).toBe(1);
    expect(emitted).toEqual([
      expect.objectContaining({
        type: 'operation_terminated',
        data: {},
        metadata: {},
        sessionId: '',
      }),
      expect.objectContaining({
        type: 'operation_finalized',
        data: {},
        metadata: {},
        sessionId: '',
      }),
    ]);
  });
});
