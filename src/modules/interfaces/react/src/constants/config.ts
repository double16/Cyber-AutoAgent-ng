/**
 * Configuration constants - centralized defaults and limits
 */

// Display limits
export const DISPLAY_LIMITS = {
  TRUNCATE_SHORT: 50,
  TRUNCATE_MEDIUM: 80,
  TRUNCATE_LONG: 100,
  TRUNCATE_EXTENDED: 200,
  CODE_PREVIEW_LINES: 8,
  TOOL_INPUT_MAX_KEYS: 4,
  TOOL_INPUT_PREVIEW_KEYS: 3,
  // Report display limits
  REPORT_MAX_LINES: Infinity,  // Never collapse final reports
  REPORT_PREVIEW_LINES: 100,  // Show first 100 lines when collapsed (unused for final reports)
  REPORT_TAIL_LINES: 20,  // Show last 20 lines when collapsed (unused for final reports)
  // report_content event limits (inline display from event payload)
  REPORT_CONTENT_MAX_LINES: 150,  // Maximum lines to show inline for report_content events
  REPORT_CONTENT_PREVIEW_LINES: 100,  // Show first 100 lines
  REPORT_CONTENT_TAIL_LINES: 30,  // Show last 30 lines when truncated
  REPORT_CONTENT_MAX_LINE_LENGTH: 320,  // Maximum characters per line
  REPORT_CONTENT_MAX_TOTAL_CHARS: 30000,  // Maximum total characters to render inline
  OPERATION_SUMMARY_LINES: 200,  // Show all operation summary info including paths
  DEFAULT_COLLAPSE_LINES: 20,  // Normal output collapse threshold
  TOOL_OUTPUT_COLLAPSE_LINES: 300,  // Collapse tool outputs beyond this many lines
  TOOL_OUTPUT_PREVIEW_LINES: 200,     // Show first 200 lines for tool outputs when collapsed
  TOOL_OUTPUT_TAIL_LINES: 50,         // Show last 50 lines for tool outputs when collapsed
  // Stream display limits
  REASONING_MAX_LINES: 30,  // Maximum lines to show for reasoning
  OUTPUT_MAX_LINES: 150,  // Maximum lines to show for output (increased for tool outputs)
  // Char-based fallback truncation for single-line or minified outputs
  OUTPUT_PREVIEW_CHARS: 2000,
  OUTPUT_TAIL_CHARS: 500,
} as const;

// Event types for consistency
export const EVENT_TYPES = {
  // Core events
  STEP_HEADER: 'step_header',
  REASONING: 'reasoning',
  THINKING: 'thinking',
  THINKING_END: 'thinking_end',
  TOOL_START: 'tool_start',
  TOOL_END: 'tool_end',
  OUTPUT: 'output',
  ERROR: 'error',
  METADATA: 'metadata',
  DIVIDER: 'divider',
  RATE_LIMIT: 'rate_limit',

  // Swarm events
  SWARM_START: 'swarm_start',
  SWARM_HANDOFF: 'swarm_handoff',
  SWARM_COMPLETE: 'swarm_complete',

  // User interaction
  USER_HANDOFF: 'user_handoff',
  USER_INPUT: 'user_input',

  // Metrics
  METRICS_UPDATE: 'metrics_update',
  EVALUATION_COMPLETE: 'evaluation_complete',

  // SDK events
  MODEL_INVOCATION_START: 'model_invocation_start',
  MODEL_STREAM_DELTA: 'model_stream_delta',
  REASONING_DELTA: 'reasoning_delta',
  TOOL_INVOCATION_START: 'tool_invocation_start',
  TOOL_INVOCATION_END: 'tool_invocation_end',
  EVENT_LOOP_CYCLE_START: 'event_loop_cycle_start',
  CONTENT_BLOCK_DELTA: 'content_block_delta',
} as const;

// Status codes
export const STATUS = {
  SUCCESS: 'success',
  ERROR: 'error',
  WARNING: 'warning',
  INFO: 'info',
  PENDING: 'pending',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
} as const;
