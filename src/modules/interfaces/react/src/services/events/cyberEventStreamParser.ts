export interface CyberEventStreamParserState {
  streamEventBuffer: string;
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

const EVENT_REGEX = /__CYBER_EVENT__(.+?)__CYBER_EVENT_END__/s;
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
        content: `◆ ${eventData.tool_name} ${JSON.stringify(eventData.tool_input)}`,
        timestamp: Date.now(),
      });
    }
    return;
  }

  if (
    eventData.type === 'tool_invocation_end' ||
    eventData.type === 'tool_result' ||
    eventData.type === 'step_header' ||
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
      emitEvent({
        type: 'output',
        content: eventData.success ? `✓ ${eventData.tool_name}` : `○ ${eventData.tool_name}`,
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

  let match: RegExpExecArray | null;
  let processedEvents = false;

  while ((match = EVENT_REGEX.exec(state.streamEventBuffer)) !== null) {
    processedEvents = true;
    const start = match.index;
    const end = start + match[0].length;
    appendRawToolOutput(state, handlers.emitEvent, state.streamEventBuffer.slice(0, start));

    try {
      const eventData = JSON.parse(match[1]);
      updateToolExecutionState(state, handlers.emitEvent, eventData);
      handlers.handleEvent(eventData);
      state.streamEventBuffer = state.streamEventBuffer.slice(end);
      handlers.onAfterParsedEvent?.();
    } catch (error) {
      handlers.onParseError(error, match[1]);
      state.streamEventBuffer = state.streamEventBuffer.slice(end);
    }
  }

  handlers.onAfterChunk?.();

  if (!processedEvents && cleanedData) {
    appendRawToolOutput(state, handlers.emitEvent, cleanedData);
    state.streamEventBuffer = '';
  }

  const maxStreamBuffer = handlers.maxStreamBuffer ?? DEFAULT_MAX_STREAM_BUFFER;
  if (state.streamEventBuffer.length > maxStreamBuffer) {
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
    emitEvent({
      type: 'output',
      content: '◆ Loading cybersecurity assessment tools:',
      timestamp: Date.now(),
    });
  } else if (event.type === 'tool_available') {
    emitEvent({
      type: 'output',
      content: `  ✓ ${eventData.tool_name} (${eventData.description})`,
      timestamp: Date.now(),
    });
  } else if (event.type === 'tool_unavailable') {
    emitEvent({
      type: 'output',
      content: `  ○ ${eventData.tool_name} (${eventData.description || ''}) - unavailable`,
      timestamp: Date.now(),
    });
  } else if (event.type === 'environment_ready') {
    emitEvent({
      type: 'output',
      content: `◆ Environment ready - ${eventData.tool_count} cybersecurity tools loaded`,
      timestamp: Date.now(),
    });
  } else if (event.type === 'operation_complete' || event.type === 'assessment_complete') {
    handlers.onComplete?.();
  } else if (event.type === 'user_handoff') {
    handlers.onUserHandoff?.();
  }

  emitEvent(event);
}
