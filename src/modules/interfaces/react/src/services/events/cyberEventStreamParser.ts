export interface CyberEventStreamParserState {
  streamEventBuffer: string;
  discardingTruncatedEvent?: boolean;
  inToolExecution: boolean;
  toolOutputBuffer: string;
  sawBackendToolOutput: boolean;
  currentToolName?: string;
}

export interface CyberEventStreamParserHandlers {
  emitEvent: (event: any) => void;
  handleEvent: (eventData: any) => void;
  onParseError: (error: unknown, rawEvent: string) => void;
  sanitizeInput?: (data: string) => string;
  onAfterParsedEvent?: () => void;
  onAfterChunk?: () => void;
  maxStreamBuffer?: number;
  streamBufferTail?: number;
}

const EVENT_START = '__CYBER_EVENT__';
const EVENT_END = '__CYBER_EVENT_END__';
const MAX_TOOL_OUTPUT = 1 * 1024 * 1024;
const TOOL_OUTPUT_CHUNK_SIZE = 64 * 1024;
const TOOL_OUTPUT_MIN_SPLIT = 32 * 1024;
const DEFAULT_MAX_STREAM_BUFFER = 32 * 1024;
const DEFAULT_STREAM_BUFFER_TAIL = 16 * 1024;

function emitToolOutputChunk(
  state: CyberEventStreamParserState,
  emitEvent: CyberEventStreamParserHandlers['emitEvent'],
  content: string
): void {
  try {
    emitEvent({
      type: 'output',
      content,
      timestamp: Date.now(),
      metadata: { fromToolBuffer: true, tool: state.currentToolName, chunked: true },
    });
  } catch {}
}

function flushToolOutputChunks(
  state: CyberEventStreamParserState,
  emitEvent: CyberEventStreamParserHandlers['emitEvent'],
  force = false
): void {
  while (
    state.toolOutputBuffer.length > TOOL_OUTPUT_CHUNK_SIZE ||
    (force && state.toolOutputBuffer.length > 0)
  ) {
    const window = state.toolOutputBuffer.slice(0, TOOL_OUTPUT_CHUNK_SIZE);
    let chunkLength = Math.min(state.toolOutputBuffer.length, TOOL_OUTPUT_CHUNK_SIZE);
    const newlineIndex = window.lastIndexOf('\n');
    if (newlineIndex >= TOOL_OUTPUT_MIN_SPLIT && newlineIndex < TOOL_OUTPUT_CHUNK_SIZE) {
      chunkLength = newlineIndex + 1;
    }

    const chunk = state.toolOutputBuffer.slice(0, chunkLength);
    emitToolOutputChunk(state, emitEvent, chunk);
    state.toolOutputBuffer = state.toolOutputBuffer.slice(chunkLength);
  }
}

function appendRawToolOutput(
  state: CyberEventStreamParserState,
  emitEvent: CyberEventStreamParserHandlers['emitEvent'],
  text: string
): void {
  if (!text || !state.inToolExecution) {
    return;
  }

  state.toolOutputBuffer += text;
  if (state.toolOutputBuffer.length > MAX_TOOL_OUTPUT) {
    state.toolOutputBuffer = state.toolOutputBuffer.slice(-MAX_TOOL_OUTPUT);
  }
  flushToolOutputChunks(state, emitEvent, false);
}

function emitTruncatedEventNotice(
  emitEvent: CyberEventStreamParserHandlers['emitEvent']
): void {
  emitEvent({
    type: 'output',
    content: 'output truncated',
    timestamp: Date.now(),
    metadata: { truncated: true },
  });
}

function retainPossibleEventStart(buffer: string): string {
  const maxSuffixLength = Math.min(buffer.length, EVENT_START.length - 1);
  for (let length = maxSuffixLength; length > 0; length -= 1) {
    if (buffer.endsWith(EVENT_START.slice(0, length))) {
      return buffer.slice(-length);
    }
  }
  return '';
}

function updateToolExecutionState(
  state: CyberEventStreamParserState,
  emitEvent: CyberEventStreamParserHandlers['emitEvent'],
  eventData: any
): void {
  if (eventData.type === 'tool_start' || eventData.type === 'tool_invocation_start') {
    state.inToolExecution = true;
    state.toolOutputBuffer = '';
    state.sawBackendToolOutput = false;
    state.currentToolName = eventData.tool_name || eventData.toolName || eventData.tool || undefined;

    if (eventData.type === 'tool_start') {
      emitEvent({
        type: 'output',
        content: `🔧 ${eventData.tool_name} ${JSON.stringify(eventData.tool_input)}`,
        metadata: { syntheticToolStart: true },
        timestamp: Date.now(),
      });
    }
    return;
  }

  if (
    eventData.type === 'tool_invocation_end' ||
    eventData.type === 'tool_result' ||
    eventData.type === 'progress_update' ||
    eventData.type === 'tool_end'
  ) {
    if (!state.sawBackendToolOutput) {
      flushToolOutputChunks(state, emitEvent, true);
    }
    state.toolOutputBuffer = '';
    state.inToolExecution = false;
    state.sawBackendToolOutput = false;
    state.currentToolName = undefined;

    if (eventData.type === 'tool_end') {
      const completion = eventData.success
        ? `✅ ${eventData.tool_name}`
        : eventData.executed === false
          ? `🚫 ${eventData.tool_name} (${eventData.outcome === 'blocked' ? 'blocked' : 'input validation failed'})`
          : `❌ ${eventData.tool_name} (failed)`;
      emitEvent({
        type: 'output',
        content: completion,
        timestamp: Date.now(),
      });
    }
    return;
  }

  if (eventData.type === 'output' && eventData.metadata?.fromToolBuffer) {
    state.sawBackendToolOutput = true;
  }
}

export function processCyberEventStreamChunk(
  data: string,
  state: CyberEventStreamParserState,
  handlers: CyberEventStreamParserHandlers
): void {
  const cleanedData = handlers.sanitizeInput ? handlers.sanitizeInput(data) : data;
  state.streamEventBuffer += cleanedData;

  if (state.discardingTruncatedEvent) {
    const end = state.streamEventBuffer.indexOf(EVENT_END);
    if (end < 0) {
      state.streamEventBuffer = '';
      handlers.onAfterChunk?.();
      return;
    }
    state.streamEventBuffer = state.streamEventBuffer.slice(end + EVENT_END.length);
    state.discardingTruncatedEvent = false;
  }

  while (state.streamEventBuffer.length > 0) {
    const start = state.streamEventBuffer.indexOf(EVENT_START);

    if (start < 0) {
      const possibleStart = retainPossibleEventStart(state.streamEventBuffer);
      const rawEnd = state.streamEventBuffer.length - possibleStart.length;
      appendRawToolOutput(
        state,
        handlers.emitEvent,
        state.streamEventBuffer.slice(0, rawEnd)
      );
      state.streamEventBuffer = possibleStart;
      break;
    }

    appendRawToolOutput(
      state,
      handlers.emitEvent,
      state.streamEventBuffer.slice(0, start)
    );
    state.streamEventBuffer = state.streamEventBuffer.slice(start + EVENT_START.length);

    let end = state.streamEventBuffer.indexOf(EVENT_END);
    if (end < 0) {
      state.streamEventBuffer = `${EVENT_START}${state.streamEventBuffer}`;
      const maxStreamBuffer = handlers.maxStreamBuffer ?? DEFAULT_MAX_STREAM_BUFFER;
      if (state.streamEventBuffer.length > maxStreamBuffer) {
        emitTruncatedEventNotice(handlers.emitEvent);
        state.streamEventBuffer = '';
        state.discardingTruncatedEvent = true;
      }
      break;
    }

    const firstEnd = end;
    const firstPayload = state.streamEventBuffer.slice(0, firstEnd);
    let eventData: any;
    let parseError: unknown;
    let payloadEnd = end;

    // A marker can occur inside a JSON string value. Try later end markers
    // before classifying the frame as malformed.
    while (end >= 0) {
      const payload = state.streamEventBuffer.slice(0, end);
      try {
        eventData = JSON.parse(payload);
        payloadEnd = end;
        break;
      } catch (error) {
        parseError ??= error;
        end = state.streamEventBuffer.indexOf(EVENT_END, end + EVENT_END.length);
      }
    }

    if (eventData !== undefined) {
      updateToolExecutionState(state, handlers.emitEvent, eventData);
      handlers.handleEvent(eventData);
      state.streamEventBuffer = state.streamEventBuffer.slice(
        payloadEnd + EVENT_END.length
      );
      handlers.onAfterParsedEvent?.();
    } else {
      handlers.onParseError(parseError, firstPayload);
      state.streamEventBuffer = state.streamEventBuffer.slice(
        firstEnd + EVENT_END.length
      );
    }
  }

  handlers.onAfterChunk?.();

  const maxStreamBuffer = handlers.maxStreamBuffer ?? DEFAULT_MAX_STREAM_BUFFER;
  if (
    state.streamEventBuffer.length > maxStreamBuffer
    && !state.streamEventBuffer.startsWith(EVENT_START)
  ) {
    state.streamEventBuffer = state.streamEventBuffer.slice(
      -(handlers.streamBufferTail ?? DEFAULT_STREAM_BUFFER_TAIL)
    );
  }
}

export function createParsedEvent(eventData: any): any {
  return {
    type: eventData.type,
    content: eventData.content,
    data: eventData.data || {},
    metadata: eventData.metadata || {},
    timestamp: eventData.timestamp || Date.now(),
    id: eventData.id || `evt-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    sessionId: eventData.sessionId || '',
    ...eventData,
  };
}

export interface ExecutionServiceEventHandlers {
  emitEvent: CyberEventStreamParserHandlers['emitEvent'];
  onComplete?: () => void;
  onUserHandoff?: () => void;
}

export function emitStatusEvents(
  eventData: any,
  handlers: ExecutionServiceEventHandlers
): void {
  const { emitEvent } = handlers;
  const event = createParsedEvent(eventData);

  if (event.type === 'tool_discovery_start') {
    const message = eventData.message || 'Loading cybersecurity assessment tools';
    emitEvent({
      type: 'output',
      content: `🔎 ${message}`,
      timestamp: Date.now(),
    });
  } else if (event.type === 'tool_available') {
    emitEvent({
      type: 'output',
      content: `  🔧 ${eventData.tool_name} (${eventData.description})`,
      timestamp: Date.now(),
    });
  } else if (event.type === 'tool_unavailable') {
    emitEvent({
      type: 'output',
      content: `  ⛔ ${eventData.tool_name} (${eventData.description || ''}) - unavailable`,
      timestamp: Date.now(),
    });
  } else if (event.type === 'environment_ready') {
    const message = eventData.message || `Environment ready - ${eventData.tool_count} cybersecurity tools loaded`;
    emitEvent({
      type: 'output',
      content: `🟢 ${message}`,
      timestamp: Date.now(),
    });
  } else if (event.type === 'operation_complete' || event.type === 'assessment_complete') {
    handlers.onComplete?.();
  } else if (event.type === 'user_handoff') {
    handlers.onUserHandoff?.();
  }

  emitEvent(event);
}
