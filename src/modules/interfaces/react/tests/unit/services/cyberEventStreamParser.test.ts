import { describe, expect, it, jest } from '@jest/globals';
import {
  processCyberEventStreamChunk,
  CyberEventStreamParserState,
} from '../../../src/services/events/cyberEventStreamParser.js';

const start = '__CYBER_EVENT__';
const end = '__CYBER_EVENT_END__';

function createState(): CyberEventStreamParserState {
  return {
    streamEventBuffer: '',
    inToolExecution: false,
    toolOutputBuffer: '',
    sawBackendToolOutput: false,
  };
}

function createHandlers(events: any[], parseErrors: string[]) {
  return {
    emitEvent: (event: any) => events.push(event),
    handleEvent: (event: any) => events.push({ handled: event }),
    onParseError: (_error: unknown, rawEvent: string) => parseErrors.push(rawEvent),
  };
}

describe('cyber event stream framing', () => {
  it('keeps a structured event across input chunks', () => {
    const state = createState();
    const events: any[] = [];
    const parseErrors: string[] = [];
    const handlers = createHandlers(events, parseErrors);
    const frame = `${start}{"type":"status","content":"hello"}${end}`;

    processCyberEventStreamChunk(frame.slice(0, 18), state, handlers);
    processCyberEventStreamChunk(frame.slice(18), state, handlers);

    expect(events).toEqual([{ handled: { type: 'status', content: 'hello' } }]);
    expect(parseErrors).toHaveLength(0);
  });

  it('reports and skips an oversized incomplete frame without parsing it', () => {
    const state = createState();
    const events: any[] = [];
    const parseErrors: string[] = [];
    const handlers = createHandlers(events, parseErrors);

    processCyberEventStreamChunk(`${start}{"type":"output","content":"${'x'.repeat(80)}`, state, {
      ...handlers,
      maxStreamBuffer: 32,
    });
    processCyberEventStreamChunk(
      `discarded payload${end}${start}{"type":"status","content":"recovered"}${end}`,
      state,
      handlers
    );

    expect(events).toEqual([
      expect.objectContaining({ type: 'output', content: 'output truncated' }),
      { handled: { type: 'status', content: 'recovered' } },
    ]);
    expect(parseErrors).toHaveLength(0);
  });

  it('does not mistake an end marker inside JSON content for the frame boundary', () => {
    const state = createState();
    const events: any[] = [];
    const parseErrors: string[] = [];
    const handlers = createHandlers(events, parseErrors);
    const frame = `${start}${JSON.stringify({ type: 'output', content: `before ${end} after` })}${end}`;

    processCyberEventStreamChunk(frame, state, handlers);

    expect(events).toEqual([
      { handled: { type: 'output', content: `before ${end} after` } },
    ]);
    expect(parseErrors).toHaveLength(0);
  });

  it('still reports malformed complete frames', () => {
    const state = createState();
    const events: any[] = [];
    const parseErrors: string[] = [];
    const handlers = createHandlers(events, parseErrors);
    const onParseError = jest.fn(handlers.onParseError);

    processCyberEventStreamChunk(`${start}{"type":${end}`, state, {
      ...handlers,
      onParseError,
    });

    expect(onParseError).toHaveBeenCalledTimes(1);
    expect(events).toHaveLength(0);
  });
});
