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
    service[method](`raw output\n${wrapEvent({ type: 'step_header', content: 'next', timestamp: 2 })}`);

    const chunks = emitted.filter(event => event?.metadata?.fromToolBuffer);
    expect(chunks).toHaveLength(1);
    expect(chunks[0].content).toBe('raw output\n');
    expect(chunks[0].metadata.tool).toBe(toolName);
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
  ])('%s uses Docker-preferred status event formatting and pass-through', (_name, createService, method) => {
    const service: any = createService();
    const emitted = captureEvents(service);

    service[method](wrapEvent({ type: 'tool_unavailable', tool_name: 'scanner', timestamp: 5 }));

    expect(emitted).toEqual([
      expect.objectContaining({
        type: 'output',
        content: '  ○ scanner () - unavailable',
      }),
      expect.objectContaining({
        type: 'tool_unavailable',
        tool_name: 'scanner',
        data: {},
        metadata: {},
        sessionId: '',
      }),
    ]);
  });

  it.each([
    ['DirectDockerService', () => new DirectDockerService(), 'parseEvents'],
    ['PythonExecutionService', () => new PythonExecutionService(), 'processOutputStream'],
  ])('%s emits completion for operation_complete events', (_name, createService, method) => {
    const service: any = createService();
    const emitted = captureEvents(service);
    let completeCount = 0;
    service.on('complete', () => completeCount += 1);

    service[method](wrapEvent({ type: 'operation_complete', timestamp: 7 }));

    expect(completeCount).toBe(1);
    expect(emitted).toEqual([
      expect.objectContaining({
        type: 'operation_complete',
        data: {},
        metadata: {},
        sessionId: '',
      }),
    ]);
  });
});
