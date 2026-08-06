/**
 * Terminal - Full terminal buffer streaming display
 *
 * Uses React Ink's Static component for smooth output without height limits.
 *
 * This component allows the full agent output to flow naturally without
 * artificial height constraints, preventing text overlap and cutoff issues.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Box } from 'ink';
import { StreamDisplay, StaticStreamDisplay, DisplayStreamEvent } from './StreamDisplay.js';
import { ExecutionService } from '../services/ExecutionService.js';
import { themeManager } from '../themes/theme-manager.js';
import { loggingService } from '../services/LoggingService.js';
import { normalizeEvent } from '../services/events/normalize.js';
import { RingBuffer } from '../utils/RingBuffer.js';
import { ByteBudgetRingBuffer } from '../utils/ByteBudgetRingBuffer.js';
import { DISPLAY_LIMITS } from '../constants/config.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import type { ThinkingContext, ThinkingStatus } from '../types/thinking.js';
import {
  formatOperationHealth,
  type OperationHealthSnapshot,
} from '../utils/operationHealthFormatting.js';

// Exported helper: build a trimmed report preview to avoid storing huge content in memory
export const buildTrimmedReportContent = (raw: string): string => {
  try {
    const normalized = String(raw || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const lines = normalized.split('\n');
    const head = DISPLAY_LIMITS.REPORT_PREVIEW_LINES || 100;
    const tail = DISPLAY_LIMITS.REPORT_TAIL_LINES || 20;
    const lineTrimmed = lines.length <= head + tail ? normalized : [
      ...lines.slice(0, head),
      '',
      '... (content continues)',
      '',
      ...lines.slice(-tail)
    ].join('\n');
    const maxChars = DISPLAY_LIMITS.REPORT_CONTENT_MAX_TOTAL_CHARS || 30000;
    if (lineTrimmed.length <= maxChars) return lineTrimmed;
    return trimStringForMemory(lineTrimmed, maxChars);
  } catch {
    return String(raw || '');
  }
};

const DEFAULT_FINAL_REPORT_EVENT_BYTES = 2 * 1024 * 1024;
const DEFAULT_REASONING_BUFFER_CHARS = 120000;
const JSON_ESTIMATE_LIMIT = 64 * 1024 * 1024;

const WORKFLOW_ACTIVITY_TERMINAL_STATUSES = new Set([
  'completed',
  'failed',
  'cancelled',
  'canceled',
  'skipped',
  'terminated',
  'done',
  'success',
  'error',
  'partial_failure',
  'blocked',
  'not_applicable',
]);

const workflowActivityKey = (event: any): string => JSON.stringify([
  event?.role ?? event?.action ?? event?.activity ?? 'workflow',
  event?.phase_id ?? null,
  event?.task_uid ?? event?.task_title ?? null,
  event?.batch_index ?? null,
  event?.attempt ?? null,
]);

const isWorkflowActivityTerminal = (event: any): boolean =>
  WORKFLOW_ACTIVITY_TERMINAL_STATUSES.has(String(event?.status || '').trim().toLowerCase());

const estimateJsonBytes = (value: unknown, maxBytes = JSON_ESTIMATE_LIMIT): number => {
  try {
    const seen = new WeakSet<object>();
    const estimate = (nested: unknown, depth: number): number => {
      if (nested == null) return 4;
      if (typeof nested === 'string') return Math.min(nested.length, maxBytes);
      if (typeof nested === 'number' || typeof nested === 'boolean') return 16;
      if (typeof nested !== 'object') return 32;
      if (seen.has(nested)) return 16;
      if (depth <= 0) return 128;
      seen.add(nested);

      let total = Array.isArray(nested) ? 32 : 64;
      if (Array.isArray(nested)) {
        for (const item of nested.slice(0, 200)) {
          total += estimate(item, depth - 1);
          if (total >= maxBytes) return maxBytes;
        }
        if (nested.length > 200) total += 64;
      } else {
        for (const [key, item] of Object.entries(nested as Record<string, unknown>)) {
          total += key.length + estimate(item, depth - 1);
          if (total >= maxBytes) return maxBytes;
        }
      }
      return Math.min(total, maxBytes);
    };
    return estimate(value, 5);
  } catch {
    return 256;
  }
};

const safeJsonPreview = (value: unknown, maxChars = 8192): string => {
  try {
    const seen = new WeakSet<object>();
    const summarize = (nested: unknown, depth: number): unknown => {
      if (typeof nested === 'string') return trimStringForMemory(nested, 512);
      if (!nested || typeof nested !== 'object') return nested;
      if (seen.has(nested)) return '[Circular]';
      if (depth <= 0) return '[Object]';
      seen.add(nested);
      if (Array.isArray(nested)) {
        return {
          length: nested.length,
          preview: nested.slice(0, 5).map(item => summarize(item, depth - 1)),
        };
      }
      const out: Record<string, unknown> = {};
      for (const [key, item] of Object.entries(nested as Record<string, unknown>).slice(0, 12)) {
        out[key] = summarize(item, depth - 1);
      }
      return out;
    };
    const json = JSON.stringify(summarize(value, 4));
    return (json || '').slice(0, maxChars);
  } catch {
    return '[Unserializable object]';
  }
};

export const estimateDisplayEventBytes = (event: DisplayStreamEvent | null | undefined): number => {
  if (!event) return 0;
  let bytes = 128;
  const anyEvent = event as any;
  const addValue = (value: unknown) => {
    if (typeof value === 'string') {
      bytes += value.length;
    } else if (value && typeof value === 'object') {
      bytes += estimateJsonBytes(value);
    } else if (value !== undefined && value !== null) {
      bytes += 16;
    }
  };

  addValue(anyEvent.content);
  addValue(anyEvent.command);
  addValue(anyEvent.message);
  addValue(anyEvent.delta);
  addValue(anyEvent.output);
  addValue(anyEvent.tool_input);
  addValue(anyEvent.toolInput);
  addValue(anyEvent.input);
  addValue(anyEvent.metadata);
  addValue(anyEvent.metrics);
  addValue(anyEvent.reportPath);
  addValue(anyEvent.logPath);
  addValue(anyEvent.memoryPath);
  return bytes;
};

const trimStringForMemory = (
  value: string,
  maxChars = Math.max(
    DISPLAY_LIMITS.OUTPUT_PREVIEW_CHARS + DISPLAY_LIMITS.OUTPUT_TAIL_CHARS + 80,
    4096
  )
): string => {
  if (value.length <= maxChars) return value;
  const headChars = Math.max(1, Math.floor(maxChars * 0.7));
  const tailChars = Math.max(1, maxChars - headChars);
  return `${value.slice(0, headChars)}\n... (content trimmed due to memory budget)\n${value.slice(-tailChars)}`;
};

const trimNestedValueForMemory = (value: unknown): unknown => {
  if (typeof value === 'string') return trimStringForMemory(value);
  if (!value || typeof value !== 'object') return value;
  const estimatedBytes = estimateJsonBytes(value);
  if (estimatedBytes < 8192) return value;
  return {
    omitted: true,
    estimatedBytes,
    preview: trimStringForMemory(safeJsonPreview(value)),
  };
};

export const trimDisplayEventForMemory = (event: DisplayStreamEvent): DisplayStreamEvent => {
  try {
    const anyEvent: any = event as any;
    const next: any = { ...anyEvent };
    if (next.type === 'report_content' && typeof next.content === 'string') {
      next.content = buildTrimmedReportContent(next.content);
    } else if (typeof next.content === 'string') {
      next.content = trimStringForMemory(next.content);
    } else if (next.content) {
      next.content = trimNestedValueForMemory(next.content);
    }

    for (const key of ['output', 'command', 'message', 'delta', 'tool_input', 'toolInput', 'input', 'metadata']) {
      if (next[key] !== undefined) {
        next[key] = trimNestedValueForMemory(next[key]);
      }
    }
    return next as DisplayStreamEvent;
  } catch {
    return event;
  }
};

const trimReasoningText = (value: string, maxChars: number): string => {
  if (value.length <= maxChars) return value;
  const marker = '\n\n... (reasoning trimmed due to memory budget) ...\n\n';
  const available = Math.max(0, maxChars - marker.length);
  const head = Math.floor(available * 0.7);
  const tail = available - head;
  return `${value.slice(0, head)}${marker}${value.slice(-tail)}`;
};

const trimEventArrayByByteBudget = (
  events: DisplayStreamEvent[],
  maxEvents: number,
  maxBytes: number
): DisplayStreamEvent[] => {
  const out: DisplayStreamEvent[] = [];
  let bytes = 0;
  for (let i = events.length - 1; i >= 0 && out.length < maxEvents; i -= 1) {
    const event = trimDisplayEventForMemory(events[i]);
    const size = estimateDisplayEventBytes(event);
    if (out.length > 0 && bytes + size > maxBytes) break;
    if (size > maxBytes && out.length === 0) continue;
    out.push(event);
    bytes += size;
  }
  return out.reverse();
};

interface TerminalProps {
  executionService: ExecutionService | null;
  sessionId: string;
  terminalWidth?: number;
  collapsed?: boolean;
  onEvent?: (event: any) => void;
  onMetricsUpdate?: (metrics: { tokens?: number; cost?: number; duration: string; memoryOps: number; evidence: number; progressPercent?: number }) => void;
  onHealthUpdate?: (health: OperationHealthSnapshot) => void;
  onThinkingUpdate?: (status: ThinkingStatus) => void;
  animationsEnabled?: boolean;
  cleanupRef?: React.MutableRefObject<(() => void) | null>;
}

export const Terminal: React.FC<TerminalProps> = React.memo(({
  executionService,
  sessionId,
  terminalWidth: propsTerminalWidth,
  collapsed = false,
  onEvent,
  onMetricsUpdate,
  onHealthUpdate,
  onThinkingUpdate,
  animationsEnabled = true,
  cleanupRef
}) => {
  // Use production-grade terminal size hook with resize handling
  const { availableWidth, availableHeight, columns } = useTerminalSize();
  const terminalWidth = propsTerminalWidth || availableWidth;
  // Test marker utility for diagnosing spinner/timer behavior
  const emitTestMarker = (msg: string) => {
    try {
      if (process.env.CYBER_TEST_MODE === 'true') {
        const marker = `[TEST_EVENT] ${msg}`;
        loggingService.info(marker);
        // eslint-disable-next-line no-console
        console.log(marker);
      }
    } catch {}
  };
  // Completed history uses append-style Static rendering; live events stay dynamic.
  // Limit event buffer to prevent memory leaks - events are already persisted to disk
  // Use stricter defaults for docker-stack (full-stack) mode
  const serviceMode = (executionService && typeof (executionService as any).getMode === 'function')
    ? (executionService as any).getMode()
    : undefined;
  const isDockerStack = serviceMode === 'docker-stack';
  const MAX_EVENTS = Number(process.env.CYBER_MAX_EVENTS || (isDockerStack ? 2000 : 3000)); // Keep last N events
  const MAX_FINAL_REPORT_EVENTS = Number(process.env.CYBER_MAX_FINAL_REPORT_EVENTS || 300);
  const MAX_FINAL_REPORT_EVENT_BYTES = Number(
    process.env.CYBER_MAX_FINAL_REPORT_EVENT_BYTES || DEFAULT_FINAL_REPORT_EVENT_BYTES
  );
  const MAX_REASONING_BUFFER_CHARS = Number(
    process.env.CYBER_MAX_REASONING_BUFFER_CHARS || DEFAULT_REASONING_BUFFER_CHARS
  );
  const MAX_GLOBAL_OUTPUT_FINGERPRINTS = Number(process.env.CYBER_MAX_GLOBAL_OUTPUT_FINGERPRINTS || 5000);
  const MAX_TOOL_OUTPUT_FINGERPRINTS = Number(process.env.CYBER_MAX_TOOL_OUTPUT_FINGERPRINTS || 1000);
  const MAX_TOOL_DEDUPE_SESSIONS = Number(process.env.CYBER_MAX_TOOL_DEDUPE_SESSIONS || 100);
  const MAX_OPERATION_SUMMARY_EVENTS = Number(process.env.CYBER_MAX_OPERATION_SUMMARY_EVENTS || 20);
  const [completedEvents, setCompletedEvents] = useState<DisplayStreamEvent[]>([]);
  const [activeEvents, setActiveEvents] = useState<DisplayStreamEvent[]>([]);
  // Dedicated completion-phase buffer for FINAL REPORT, evaluation progress,
  // and the inline preview. Keeping these events together preserves arrival order.
  const [completionPhaseEvents, setCompletionPhaseEvents] = useState<DisplayStreamEvent[] | null>(null);
  const [staticSessionKey, setStaticSessionKey] = useState(0);

  // Ring buffers to bound memory regardless of session length
  const MAX_EVENT_BYTES = Number(process.env.CYBER_MAX_EVENT_BYTES || (isDockerStack ? 4 * 1024 * 1024 : 8 * 1024 * 1024)); // tighter cap in full-stack
  const completedBufRef = useRef(new ByteBudgetRingBuffer<DisplayStreamEvent>(
    MAX_EVENT_BYTES,
    {
      estimator: estimateDisplayEventBytes,
      overflowReducer: trimDisplayEventForMemory
    }
  ));
  const activeBufRef = useRef(new RingBuffer<DisplayStreamEvent>(Math.min(200, Math.floor(MAX_EVENTS / 5))));
  const appendCompletionPhaseEvent = useCallback((event: DisplayStreamEvent) => {
    setCompletionPhaseEvents(prev => {
      const next = [...(prev ?? []), event];
      return trimEventArrayByByteBudget(next, MAX_FINAL_REPORT_EVENTS, MAX_FINAL_REPORT_EVENT_BYTES);
    });
  }, [MAX_FINAL_REPORT_EVENTS, MAX_FINAL_REPORT_EVENT_BYTES]);
  const [metrics, setMetrics] = useState({
    tokens: 0,
    cost: 0,
    duration: '0s',
    memoryOps: 0,
    evidence: 0,
    progressPercent: 0
  });
  
  // Deduplication state: track seen output fingerprints per tool session and globally
  const perToolOutputSeenRef = useRef<Map<string, Set<string>>>(new Map());
  const globalOutputSeenRef = useRef<Set<string>>(new Set());
  const globalOutputSeenOrderRef = useRef<string[]>([]);
  
  // Throttle state for metrics emissions to parent
  const lastEmitRef = useRef<number>(0);
  const pendingTimerRef = useRef<NodeJS.Timeout | null>(null);
  const pendingMetricsRef = useRef<{ tokens?: number; cost?: number; duration: string; memoryOps: number; evidence: number; progressPercent: number } | null>(null);
  const EMIT_INTERVAL_MS = 16;
  const METRICS_COALESCE_MS = 50;
  const lastMetricsTsRef = useRef<number>(0);
  
  // State for event processing - replacing EventAggregator with React patterns
  const [activeThinking, setActiveThinking] = useState(false);
  // Keep a ref in sync with activeThinking to avoid setState race conditions
  const activeThinkingRef = useRef(false);
  useEffect(() => { activeThinkingRef.current = activeThinking; }, [activeThinking]);
  const workflowActivityKeysRef = useRef<Set<string>>(new Set());
  const setThinkingActive = useCallback((value: boolean) => {
    activeThinkingRef.current = value;
    setActiveThinking(value);
  }, []);
  const [activeReasoning, setActiveReasoning] = useState(false);
  const currentToolIdRef = useRef<string | undefined>(undefined);
  const setCurrentToolId = (toolId: string | undefined) => {
    currentToolIdRef.current = toolId;
  };
  const lastOutputContentRef = useRef('');
  const lastOutputTimeRef = useRef(0);

  const rememberGlobalOutputFingerprint = (fingerprint: string): boolean => {
    if (globalOutputSeenRef.current.has(fingerprint)) {
      return true;
    }
    globalOutputSeenRef.current.add(fingerprint);
    globalOutputSeenOrderRef.current.push(fingerprint);
    while (globalOutputSeenOrderRef.current.length > MAX_GLOBAL_OUTPUT_FINGERPRINTS) {
      const oldest = globalOutputSeenOrderRef.current.shift();
      if (oldest) globalOutputSeenRef.current.delete(oldest);
    }
    return false;
  };

  const rememberToolOutputFingerprint = (toolId: string, fingerprint: string): boolean => {
    let seenForTool = perToolOutputSeenRef.current.get(toolId);
    if (!seenForTool) {
      seenForTool = new Set();
      perToolOutputSeenRef.current.set(toolId, seenForTool);
    }
    if (seenForTool.has(fingerprint)) {
      return true;
    }
    seenForTool.add(fingerprint);
    if (seenForTool.size > MAX_TOOL_OUTPUT_FINGERPRINTS) {
      seenForTool.clear();
      seenForTool.add(fingerprint);
    }
    while (perToolOutputSeenRef.current.size > MAX_TOOL_DEDUPE_SESSIONS) {
      const oldestKey = perToolOutputSeenRef.current.keys().next().value;
      if (!oldestKey) break;
      perToolOutputSeenRef.current.delete(oldestKey);
    }
    return false;
  };

  // Per-step aggregated output (to display a single 'output' block per step)
  const stepAggRef = useRef<{ step?: number | null; head: string; tail: string; omitted: number } | null>({ step: null, head: '', tail: '', omitted: 0 });
  const appendToStepAgg = (fragment: string) => {
    try {
      if (!stepAggRef.current) stepAggRef.current = { step: null, head: '', tail: '', omitted: 0 };
      const agg = stepAggRef.current!;
      const s = String(fragment || '');
      if (!s) return;
      const HEAD_MAX = DISPLAY_LIMITS.OUTPUT_PREVIEW_CHARS || 2000;
      const TAIL_MAX = DISPLAY_LIMITS.OUTPUT_TAIL_CHARS || 500;
      // Fill head until full, then accumulate tail and omitted count
      if (agg.head.length < HEAD_MAX) {
        const remaining = HEAD_MAX - agg.head.length;
        agg.head += s.slice(0, remaining);
        const rest = s.slice(remaining);
        if (rest.length > 0) {
          // We have overflow; add to tail with rolling window
          if (rest.length >= TAIL_MAX) {
            agg.tail = rest.slice(-TAIL_MAX);
          } else {
            const combined = (agg.tail + rest);
            agg.tail = combined.length > TAIL_MAX ? combined.slice(-TAIL_MAX) : combined;
          }
          agg.omitted += rest.length;
        }
      } else {
        // Head full; maintain rolling tail window
        if (s.length >= TAIL_MAX) {
          agg.tail = s.slice(-TAIL_MAX);
        } else {
          const combined = (agg.tail + s);
          agg.tail = combined.length > TAIL_MAX ? combined.slice(-TAIL_MAX) : combined;
        }
        agg.omitted += s.length;
      }
    } catch {}
  };
  const buildAggDisplayEvent = (): DisplayStreamEvent | null => {
    try {
      const agg = stepAggRef.current;
      if (!agg) return null;
      const parts: string[] = [];
      if (agg.head) parts.push(agg.head);
      if (agg.omitted > 0) parts.push(`... (content continues; ${agg.omitted} chars omitted)`);
      if (agg.tail) parts.push(agg.tail);
      const content = parts.join('\n');
      if (!content) return null;
      return { type: 'output', content, metadata: { aggregated: true, fromToolBuffer: true } } as any;
    } catch { return null; }
  };
  const flushAggregatedOutput = (): DisplayStreamEvent | null => {
    const agg = buildAggDisplayEvent();
    resetStepAgg();
    return agg;
  };
  const resetStepAgg = () => { stepAggRef.current = { step: null, head: '', tail: '', omitted: 0 }; };
  
  const delayedThinkingTimerRef = useRef<NodeJS.Timeout | null>(null);
  const completionCleanupTimerRef = useRef<NodeJS.Timeout | null>(null);
  // Track whether we're currently within the FINAL REPORT phase so we can
  // accumulate a dynamic event cluster for inline preview rendering.
  const completionPhaseActiveRef = useRef<boolean>(false);
  // Timer to detect idle gaps after tool-buffer output when no explicit tool_end is emitted
  const postToolIdleTimerRef = useRef<NodeJS.Timeout | null>(null);
  // Timer to bridge the gap AFTER reasoning completes and BEFORE next step/tool begins
  const postReasoningIdleTimerRef = useRef<NodeJS.Timeout | null>(null);
  const seenThinkingThisPhaseRef = useRef<boolean>(false);
  const suppressTerminationBannerRef = useRef<boolean>(false);
  // Track last reasoning text to prevent duplicate consecutive emissions
  const lastReasoningTextRef = useRef<string | null>(null);
  // Timestamp of the most recent tool-buffered output chunk
  const lastToolOutputTsRef = useRef<number>(0);
  // Duplicate emission resolved in the agent event handler.
  // Throttle for active tail updates when animations are disabled
  const activeUpdateTimerRef = useRef<NodeJS.Timeout | null>(null);
  const pendingActiveUpdaterRef = useRef<((prev: DisplayStreamEvent[]) => DisplayStreamEvent[]) | null>(null);
  const ACTIVE_EMIT_INTERVAL_MS = 16;

  const setActiveThrottled = (
    updater: React.SetStateAction<DisplayStreamEvent[]>
  ) => {
    const fn: (prev: DisplayStreamEvent[]) => DisplayStreamEvent[] =
      typeof updater === 'function' ? (updater as (prev: DisplayStreamEvent[]) => DisplayStreamEvent[]) : (() => updater as DisplayStreamEvent[]);
    pendingActiveUpdaterRef.current = fn;
    if (activeUpdateTimerRef.current) {
      clearTimeout(activeUpdateTimerRef.current);
    }
    activeUpdateTimerRef.current = setTimeout(() => {
      const u = pendingActiveUpdaterRef.current;
      pendingActiveUpdaterRef.current = null;
      activeUpdateTimerRef.current = null;
      if (u) {
        setActiveEvents(prev => u(prev));
      }
    }, ACTIVE_EMIT_INTERVAL_MS);
  };

  function activateThinking(
    context: ThinkingContext = 'tool_execution',
    message?: string,
    extra: Partial<DisplayStreamEvent> = {},
    immediate = false
  ) {
    setThinkingActive(true);
    onThinkingUpdate?.({
      active: true,
      context,
      message: message ?? (typeof (extra as any).message === 'string' ? (extra as any).message : undefined),
      startTime: typeof (extra as any).startTime === 'number' ? (extra as any).startTime : Date.now(),
      taskTitle: typeof (extra as any).taskTitle === 'string' ? (extra as any).taskTitle : undefined,
    });
  }

  function deactivateThinking(force = false) {
    if (!force && workflowActivityKeysRef.current.size > 0) {
      return;
    }
    setThinkingActive(false);
    onThinkingUpdate?.({ active: false });
  }

  // Batch completed events updates to prevent memory churn
  const completedUpdateTimerRef = useRef<NodeJS.Timeout | null>(null);
  const flushCompletedEventsUpdate = () => {
    if (completedUpdateTimerRef.current) {
      clearTimeout(completedUpdateTimerRef.current);
      completedUpdateTimerRef.current = null;
    }
    setCompletedEvents(completedBufRef.current.toArray());
  };
  const scheduleCompletedEventsUpdate = () => {
    if (completedUpdateTimerRef.current) return;
    completedUpdateTimerRef.current = setTimeout(() => {
      try {
        setCompletedEvents(completedBufRef.current.toArray());
      } finally {
        if (completedUpdateTimerRef.current) {
          clearTimeout(completedUpdateTimerRef.current);
          completedUpdateTimerRef.current = null;
        }
      }
    }, 33); // ~30fps coalescing
  };

  const shouldFlushCompletedImmediately = (events: DisplayStreamEvent[]): boolean => (
    events.some(event => (
      event.type === 'progress_update' ||
      event.type === 'reasoning' ||
      event.type === 'tool_start' ||
      event.type === 'tool_end' ||
      event.type === 'tool_input_update' ||
      event.type === 'tool_input_corrected' ||
      event.type === 'tool_output' ||
      event.type === 'output'
    ))
  );
  const isCompletionPhaseEvent = (event: DisplayStreamEvent): boolean => (
    (event.type === 'progress_update' && (
      (event as any).operation_stage === 'final_report' ||
      (event as any).operation_stage === 'ragas_evaluation' ||
      (event as any).step === 'FINAL REPORT'
    )) ||
    String((event as any).type) === 'evaluation_step_complete' ||
    String((event as any).type) === 'evaluation_complete' ||
    String((event as any).type) === 'assessment_complete'
  );

  // Unified helpers for delayed thinking spinner scheduling/cancellation
  const cancelDelayedThinking = () => {
    if (delayedThinkingTimerRef.current) {
      clearTimeout(delayedThinkingTimerRef.current);
      delayedThinkingTimerRef.current = null;
    }
  };

  const cancelPostToolIdleTimer = () => {
    if (postToolIdleTimerRef.current) {
      clearTimeout(postToolIdleTimerRef.current);
      postToolIdleTimerRef.current = null;
    }
  };

  const cancelPostReasoningIdleTimer = () => {
    if (postReasoningIdleTimerRef.current) {
      clearTimeout(postReasoningIdleTimerRef.current);
      postReasoningIdleTimerRef.current = null;
    }
  };

  const cancelCompletionCleanupTimer = () => {
    if (completionCleanupTimerRef.current) {
      clearTimeout(completionCleanupTimerRef.current);
      completionCleanupTimerRef.current = null;
    }
  };

  const cancelCompletedEventsUpdateTimer = () => {
    if (completedUpdateTimerRef.current) {
      clearTimeout(completedUpdateTimerRef.current);
      completedUpdateTimerRef.current = null;
    }
  };

  const scheduleDelayedThinking = (opts?: { delay?: number; context?: string; toolName?: string; toolCategory?: string; addSpacer?: boolean; }) => {
    // Always cancel any existing timer first to avoid overlap
    cancelDelayedThinking();

    const delay = Math.max(0, opts?.delay ?? 100);
    emitTestMarker && emitTestMarker(`scheduleDelayedThinking request ctx=${opts?.context || 'tool_execution'} delay=${delay}`);
    delayedThinkingTimerRef.current = setTimeout(() => {
      if (activeThinkingRef.current) {
        emitTestMarker && emitTestMarker('scheduleDelayedThinking skipped (already visible)');
        delayedThinkingTimerRef.current = null;
        return;
      }

      // Optional spacing before thinking animation to visually separate
      if (opts?.addSpacer && animationsEnabled) {
        completedBufRef.current.push({ type: 'output', content: '' } as DisplayStreamEvent);
        completedBufRef.current.push({ type: 'output', content: '' } as DisplayStreamEvent);
        scheduleCompletedEventsUpdate();
      }

      const thinkingContext = (opts?.context || 'tool_execution') as ThinkingContext;
      activateThinking(thinkingContext, undefined, {
        ...(opts?.toolName ? { toolName: opts.toolName } : {}),
        ...(opts?.toolCategory ? { toolCategory: opts.toolCategory } : {}),
      });
      seenThinkingThisPhaseRef.current = true;

      emitTestMarker && emitTestMarker(`scheduleDelayedThinking fired ctx=${thinkingContext}`);
      delayedThinkingTimerRef.current = null;
    }, delay) as unknown as NodeJS.Timeout;
  };
  
  const resetAllBuffers = useCallback((preserveEvents: DisplayStreamEvent[] = []) => {
    cancelDelayedThinking();
    cancelCompletionCleanupTimer();
    cancelCompletedEventsUpdateTimer();
    if (pendingTimerRef.current) {
      clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
    if (activeUpdateTimerRef.current) {
      clearTimeout(activeUpdateTimerRef.current);
      activeUpdateTimerRef.current = null;
    }

    stepAggRef.current = { step: null, head: '', tail: '', omitted: 0 };
    pendingReasoningsRef.current = [];
    opSummaryBufferRef.current = [];
    seenThinkingThisPhaseRef.current = false;
    suppressTerminationBannerRef.current = false;
    lastReasoningTextRef.current = null;
    completionPhaseActiveRef.current = false;
    hasSeenOperationActivityRef.current = false;
    workflowActivityKeysRef.current.clear();
    setCompletionPhaseEvents(null);
    perToolOutputSeenRef.current.clear();
    globalOutputSeenRef.current.clear();
    globalOutputSeenOrderRef.current = [];
    setCurrentToolId(undefined);
    setThinkingActive(false);
    onThinkingUpdate?.({ active: false });
    setActiveReasoning(false);
    lastOutputContentRef.current = '';
    lastOutputTimeRef.current = 0;
    pendingMetricsRef.current = null;
    lastMetricsTsRef.current = 0;
    lastEmitRef.current = 0;
    setMetrics({ tokens: 0, cost: 0, duration: '0s', memoryOps: 0, evidence: 0, progressPercent: 0 });

    completedBufRef.current.clear();
    activeBufRef.current.clear();
    if (preserveEvents.length > 0) {
      completedBufRef.current.pushMany(preserveEvents);
    }
    setStaticSessionKey(prev => prev + 1);
    flushCompletedEventsUpdate();
    setActiveEvents(activeBufRef.current.toArray());
  }, [cancelDelayedThinking, onThinkingUpdate, setActiveEvents, setCompletedEvents, setThinkingActive, setActiveReasoning, setStaticSessionKey]);
  
  // Constants for event processing
  const COMMAND_BUFFER_MS = 100;
  const OUTPUT_DEDUPE_TIME_MS = 500;
  const theme = themeManager.getCurrentTheme();

  // Expose cleanup function via ref for /clear command
  React.useEffect(() => {
    if (cleanupRef) {
      cleanupRef.current = () => {
        // Clear state arrays to release memory
        setCompletedEvents([]);
        setActiveEvents([]);

        // Reset all buffers and refs
        resetAllBuffers();
      };
    }
    return () => {
      if (cleanupRef) {
        cleanupRef.current = null;
      }
    };
  }, [cleanupRef, resetAllBuffers]);

  // Reset buffers when a new session starts to prevent memory growth across runs
  React.useEffect(() => {
    // Aggressively clear state arrays first to release memory
    setCompletedEvents([]);
    setActiveEvents([]);

    // Then reset all buffers and refs
    resetAllBuffers();

    // Force garbage collection hint by clearing large objects
    // This helps prevent heap overflow when starting multiple operations in same session
    if (global.gc) {
      try {
        global.gc();
      } catch (e) {
        // GC not available, that's okay
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Soft memory pressure monitor (opt-in via env; enabled by default in docker-stack)
  React.useEffect(() => {
    const softLimitMb = Number(process.env.CYBER_HEAP_SOFT_LIMIT_MB || (isDockerStack ? 3072 : 0));
    if (!softLimitMb || !Number.isFinite(softLimitMb) || softLimitMb <= 0) return;
    let cooling = false;
    const interval = setInterval(() => {
      try {
        const usedMb = Math.round((process.memoryUsage().heapUsed || 0) / (1024 * 1024));
        if (!cooling && usedMb > softLimitMb) {
          // Emergency prune: keep last 50 completed events and clear active tail
          const keep = 50;
          const snapshot = completedBufRef.current.toArray();
          const trimmed = snapshot.slice(-keep);
          completedBufRef.current.clear();
          for (const evt of trimmed) completedBufRef.current.push(evt);
          setCompletedEvents(trimmed);
          activeBufRef.current.clear();
          setActiveEvents([]);
          pendingReasoningsRef.current = [];
          opSummaryBufferRef.current = [];
          resetStepAgg();
          perToolOutputSeenRef.current.clear();
          globalOutputSeenRef.current.clear();
          globalOutputSeenOrderRef.current = [];
          setCompletionPhaseEvents(prev => (
            prev
              ? trimEventArrayByByteBudget(prev.slice(-20), 20, Math.floor(MAX_FINAL_REPORT_EVENT_BYTES / 2))
              : prev
          ));
          // Hint GC if available
          if (global.gc) { try { global.gc(); } catch {} }
          cooling = true;
          setTimeout(() => { cooling = false; }, 5000);
        }
      } catch {}
    }, 3000);
    return () => { try { clearInterval(interval); } catch {} };
  }, [isDockerStack]);

  // Ensure immediate visual feedback at startup: schedule a lightweight spinner
  // right after the execution begins, before any backend events (e.g., operation_init)
  // are received. This avoids a black screen during initial 3–5s setup gaps.
  React.useEffect(() => {
    if (!executionService) return;
    if (collapsed) return;
    // Only schedule if no spinner is active AND no events have rendered yet
    if (!activeThinkingRef.current && !activeReasoning && activeEvents.length === 0 && completedEvents.length === 0) {
      cancelDelayedThinking();
      activateThinking('startup', undefined, {}, true);
      seenThinkingThisPhaseRef.current = true;
    }
    // Cleanup is handled by the main effect's cleanup and cancelDelayedThinking()
    // to avoid duplicate timers and ensure consistent teardown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [executionService, sessionId, animationsEnabled, collapsed]);

  // Fingerprint helper for deduplication
  const fingerprintContent = (s: string): string => {
    try {
      const str = String(s);
      const len = str.length;
      if (len === 0) return 'len:0';
      const head = str.slice(0, 512);
      const tail = len > 512 ? str.slice(-512) : '';
      return `len:${len}|h:${head}|t:${tail}`;
    } catch {
      return 'err';
    }
  };
  
  // Step gating state (anchor progress updates until first tool signal)
  const pendingProgressUpdateRef = useRef<DisplayStreamEvent | null>(null);
  const pendingStepNumberRef = useRef<number | undefined>(undefined);
  const hasToolForPendingStepRef = useRef<boolean>(false);
  const lastEmittedStepNumberRef = useRef<number | undefined>(undefined);

  const flushPendingProgressUpdate = (collector: DisplayStreamEvent[]) => {
    if (pendingProgressUpdateRef.current && !hasToolForPendingStepRef.current) {
      collector.push(pendingProgressUpdateRef.current);
      lastEmittedStepNumberRef.current = pendingStepNumberRef.current ?? lastEmittedStepNumberRef.current;
      pendingProgressUpdateRef.current = null;
      hasToolForPendingStepRef.current = true;
    }
  };

  // Track operation metadata for synthetic headers
  const operationIdRef = useRef<string | undefined>(undefined);
  const targetRef = useRef<string | undefined>(undefined);
  const hasSeenOperationActivityRef = useRef<boolean>(false);
  const lastPushedTypeRef = useRef<string | null>(null);
  const firstHeaderSeenRef = useRef<boolean>(false);
  // Buffer for operation summary lines (paths) so we can show them after report preview/content
  const opSummaryBufferRef = useRef<DisplayStreamEvent[]>([]);

  // Pending reasoning buffer (queue): hold reasoning events after the first header
  const pendingReasoningsRef = useRef<DisplayStreamEvent[]>([]);
  const pendingReasoningTimerRef = useRef<NodeJS.Timeout | null>(null);
  const REASONING_LOOKAHEAD_MS = 1000; // kept for compatibility (no timer-based flushes currently used)

  const flushPendingReasoning = (collector: DisplayStreamEvent[]) => {
    if (pendingReasoningTimerRef.current) {
      clearTimeout(pendingReasoningTimerRef.current);
      pendingReasoningTimerRef.current = null;
    }
    if (pendingReasoningsRef.current.length > 0) {
      // Merge all pending reasoning into a single block for this step
      const parts: string[] = [];
      let last: any = null;
      for (const r of pendingReasoningsRef.current) {
        if (r && typeof (r as any).content === 'string') {
          const s = String((r as any).content).trim();
          if (s) parts.push(s);
        }
        last = r || last;
      }
      const merged = trimReasoningText(parts.join('\n\n'), MAX_REASONING_BUFFER_CHARS);
      if (merged) {
        const mergedEvent: any = { type: 'reasoning', content: merged };
        // Preserve agent context from the last reasoning in the queue if present
        if (last && (last as any).agent_name) mergedEvent.agent_name = (last as any).agent_name;
        collector.push(mergedEvent as DisplayStreamEvent);
      }
      pendingReasoningsRef.current = [];
      // Clear live reasoning preview now that it has been committed
      // Use immediate update (not throttled) to prevent duplicate display
      activeBufRef.current.clear();
      setActiveEvents(activeBufRef.current.toArray());
    }
  };

  // Event processing function - replaces EventAggregator.processEvent
  const processEvent = (event: any): DisplayStreamEvent[] => {
    const results: DisplayStreamEvent[] = [];

    // Test markers for integration tests
    const testMode = process.env.CYBER_TEST_MODE === 'true';
    const emitTestMarker = (summary: string) => {
      if (testMode) {
        try { loggingService.info(`[TEST_EVENT] ${summary}`); } catch {}
        try { console.log(`[TEST_EVENT] ${summary}`); } catch {}
      }
    };
    
    switch (event.type) {
      case 'operation_init':
        hasSeenOperationActivityRef.current = true;
        workflowActivityKeysRef.current.clear();
        // Reset all dedup sets and internal refs at operation start
        perToolOutputSeenRef.current.clear();
        globalOutputSeenRef.current.clear();
        globalOutputSeenOrderRef.current = [];
        pendingReasoningsRef.current = [];
        opSummaryBufferRef.current = [];
        
        // Reset timers
        cancelDelayedThinking();
        cancelPostToolIdleTimer();
        cancelPostReasoningIdleTimer();
        if (pendingReasoningTimerRef.current) {
          clearTimeout(pendingReasoningTimerRef.current);
          pendingReasoningTimerRef.current = null;
        }

        // Cache operation metadata
        if (typeof event.operation_id === 'string') {
          operationIdRef.current = event.operation_id;
        }
        if (typeof event.target === 'string') {
      targetRef.current = event.target;
    }
    lastPushedTypeRef.current = null;
    results.push(event as DisplayStreamEvent);

    // Show spinner immediately after operation_init while awaiting first step/reasoning
    // This covers the 10-15 second gap before the agent's first response
    // CRITICAL: Use scheduleDelayedThinking with 0 delay instead of direct manipulation
    // This ensures proper integration with the event loop and prevents race conditions
    cancelDelayedThinking();
    activateThinking('waiting');
    seenThinkingThisPhaseRef.current = true;
        break;

      case 'workflow_activity': {
        const activityKey = workflowActivityKey(event);
        const terminal = isWorkflowActivityTerminal(event);
        const hadActiveWorkflowActivity = workflowActivityKeysRef.current.size > 0;
        if (terminal) {
          workflowActivityKeysRef.current.delete(activityKey);
        } else {
          workflowActivityKeysRef.current.add(activityKey);
        }
        results.push(event as DisplayStreamEvent);

        if (!terminal) {
          const label = String(event.label || event.action || event.activity || 'Workflow activity')
            .replaceAll('_', ' ')
            .trim();
          const taskTitle = typeof event.task_title === 'string' ? event.task_title.trim() : '';
          activateThinking(
            'tool_preparation',
            taskTitle ? `${label}: ${taskTitle}` : label,
            event,
          );
        } else if (hadActiveWorkflowActivity && workflowActivityKeysRef.current.size === 0) {
          deactivateThinking(true);
        }
        break;
      }

      case 'progress_update':
        hasSeenOperationActivityRef.current = true;
        emitTestMarker(`progress_update step=${event.step ?? ''} progress=${event.progressPercent ?? ''}`);
        cancelDelayedThinking();
        cancelPostReasoningIdleTimer();
        const isReportProgress = event.operation_stage === 'final_report';
        const isEvaluationProgress = event.operation_stage === 'ragas_evaluation';
        // End any active reasoning session
        setActiveReasoning(false);
        // Reset last reasoning dedupe on new step
        lastReasoningTextRef.current = null;
        // Reset output suppression for next operation phase
        suppressTerminationBannerRef.current = false;
        
        // Push header immediately (no gating)
        // Before starting a new step, flush any pending reasoning from the previous tool call
        // so it appears at the end of the previous step (below its outputs)
        flushPendingReasoning(results);

        const headerEvent: DisplayStreamEvent = {
          type: 'progress_update',
          step: event.step,
          progressPercent: event.progressPercent,
          operation: event.operation,
          duration: event.duration,
          agent_run_id: event.agent_run_id,
          agent_name: event.agent_name,
          agent_type: event.agent_type,
          parent_agent_run_id: event.parent_agent_run_id,
          agent_sub_step: event.agent_sub_step,
          agent_total_actions: event.agent_total_actions,
          operation_stage: event.operation_stage,
          report_step_index: event.report_step_index,
          report_step_total: event.report_step_total,
          report_step_kind: event.report_step_kind,
          report_step_label: event.report_step_label,
          evaluation_step_index: event.evaluation_step_index,
          evaluation_step_total: event.evaluation_step_total,
          evaluation_step_kind: event.evaluation_step_kind,
          evaluation_scope: event.evaluation_scope,
          evaluation_metric: event.evaluation_metric,
          evaluation_step_label: event.evaluation_step_label,
          health: event.health,
        } as DisplayStreamEvent;

        results.push(headerEvent);

        // Mark entry into FINAL REPORT phase and start a dynamic cluster that
        // will be rendered via StreamDisplay with an InlineReportViewer.
        if (event.step === 'FINAL REPORT') {
          completionPhaseActiveRef.current = true;
          setCompletionPhaseEvents(prev => {
            const base = prev && prev.length > 0 ? prev.filter(e => e.type !== 'progress_update' || (e as any).step !== 'FINAL REPORT') : [];
            const next = [...base, headerEvent];
            return trimEventArrayByByteBudget(next, MAX_FINAL_REPORT_EVENTS, MAX_FINAL_REPORT_EVENT_BYTES);
          });
        } else if (isReportProgress) {
          completionPhaseActiveRef.current = true;
          appendCompletionPhaseEvent(headerEvent);
        } else if (isEvaluationProgress) {
          completionPhaseActiveRef.current = true;
          appendCompletionPhaseEvent(headerEvent);
        }

        // Mark that we've seen the first header
        firstHeaderSeenRef.current = true;
        lastPushedTypeRef.current = 'progress_update';

        // Show thinking spinner while waiting for tool selection after progress update
        // Always reset and show spinner regardless of previous thinking state
        if (isReportProgress) {
          const reportStepLabel = typeof event.report_step_label === 'string'
            ? event.report_step_label.trim()
            : '';
          activateThinking(
            'waiting',
            'Generating report',
            reportStepLabel ? { taskTitle: reportStepLabel } : {},
            true
          );
          seenThinkingThisPhaseRef.current = true;
        } else if (isEvaluationProgress) {
          const evaluationStepLabel = typeof event.evaluation_step_label === 'string'
            ? event.evaluation_step_label.trim()
            : '';
          activateThinking(
            'waiting',
            'Evaluating assessment',
            evaluationStepLabel ? { taskTitle: evaluationStepLabel } : {},
            true
          );
          seenThinkingThisPhaseRef.current = true;
        } else {
          activateThinking('tool_preparation', undefined, {}, true);
          seenThinkingThisPhaseRef.current = true;

          // Footer owns the busy indicator; do not add spinner nodes to the stream.
        }
        break;
        
        
      case 'reasoning':
        hasSeenOperationActivityRef.current = true;
        emitTestMarker('reasoning');
        const reasoningAgg = flushAggregatedOutput();
        if (reasoningAgg) {
          results.push(reasoningAgg as DisplayStreamEvent);
        }
        // Any pending post-tool idle spinner is no longer needed
        cancelPostToolIdleTimer();
        // Reset last tool output timestamp on entering reasoning
        lastToolOutputTsRef.current = 0;
        // Clear any pending post-reasoning timer before scheduling a new one
        cancelPostReasoningIdleTimer();
        // Python backend sends complete reasoning blocks
        if (event.content && event.content.trim()) {
          // Clear any active thinking animations when reasoning is shown
          if (activeThinking) {
            deactivateThinking();
          }
          // Cancel any pending delayed thinking and mark seen
          cancelDelayedThinking();
          seenThinkingThisPhaseRef.current = true;
          
          // Start reasoning session
          setActiveReasoning(true);

          const reasoningEvent: DisplayStreamEvent = {
            type: 'reasoning',
            content: trimReasoningText(String(event.content).trim(), MAX_REASONING_BUFFER_CHARS),
            agent_run_id: event.agent_run_id,
            agent_name: event.agent_name,
            agent_type: event.agent_type,
            parent_agent_run_id: event.parent_agent_run_id
          } as DisplayStreamEvent;

          // Queue reasoning for final placement under this step
          pendingReasoningsRef.current.push(reasoningEvent);
          if (
            pendingReasoningsRef.current.length > 20 ||
            pendingReasoningsRef.current.reduce((sum, item) => (
              sum + (typeof (item as any).content === 'string' ? (item as any).content.length : 0)
            ), 0) > MAX_REASONING_BUFFER_CHARS
          ) {
            pendingReasoningsRef.current = [{
              ...reasoningEvent,
              content: trimReasoningText(
                pendingReasoningsRef.current
                  .map(item => (typeof (item as any).content === 'string' ? (item as any).content.trim() : ''))
                  .filter(Boolean)
                  .join('\n\n'),
                MAX_REASONING_BUFFER_CHARS
              ),
            } as DisplayStreamEvent];
          }

          // Immediately reflect the merged reasoning in the active tail so the user sees it during this step
          try {
            const parts: string[] = [];
            for (const r of pendingReasoningsRef.current) {
              const s = (r as any).content ? String((r as any).content).trim() : '';
              if (s) parts.push(s);
            }
            const merged = trimReasoningText(parts.join('\n\n'), MAX_REASONING_BUFFER_CHARS);
            if (merged) {
              setActiveThrottled(prev => {
                activeBufRef.current.clear();
                activeBufRef.current.push({ type: 'reasoning', content: merged } as any);
                return activeBufRef.current.toArray();
              });
              // After rendering reasoning, briefly show a spinner while the agent
              // prepares the next step/tool selection to avoid a blank gap.
              cancelPostReasoningIdleTimer();
              postReasoningIdleTimerRef.current = setTimeout(() => {
                setActiveReasoning(false);
                if (!activeThinkingRef.current) {
                  activateThinking('reasoning');
                  seenThinkingThisPhaseRef.current = true;
                }
                postReasoningIdleTimerRef.current = null;
              }, 10) as unknown as NodeJS.Timeout;
            }
          } catch {}
        }
        break;
        
      case 'thinking':
        // Handle thinking start without conflicting with reasoning
        if (!activeReasoning) {
          // Cancel any pending delayed spinner to avoid duplicates
          cancelDelayedThinking();
          cancelPostToolIdleTimer();
          cancelPostReasoningIdleTimer();
          seenThinkingThisPhaseRef.current = true;
          activateThinking(event.context || 'tool_execution', event.message, event);
        }
        break;
        
      case 'thinking_end':
        deactivateThinking();
        break;
        
      case 'delayed_thinking_start':
        // Handle delayed thinking start - pass through and mark as active
        if (!activeThinking && !activeReasoning) {
          activateThinking(((event as any).context || 'tool_execution') as ThinkingContext);
        }
        break;
        
      case 'tool_start':
        hasSeenOperationActivityRef.current = true;
        emitTestMarker(`tool_start tool=${event.toolName || event.tool_name}`);
        cancelPostToolIdleTimer();
        cancelPostReasoningIdleTimer();

        // Keep spinner showing during tool execution, just change context
        if (!activeThinking) {
          activateThinking('tool_execution');
        }

        // Reset last tool output timestamp for new tool
        lastToolOutputTsRef.current = 0;
        // Do not flush pending reasoning here; wait for progress_update to ensure correct attribution
        // Get the tool ID from the event (support both camel/snake)
        let toolId: string | undefined = event.toolId || event.tool_id;
        // Some tools (e.g., orchestrators) don't emit IDs; use a stable fallback so headers render.
        if (!toolId) {
          const bucket = Math.floor((event.timestamp ? Date.parse(event.timestamp) : Date.now()) / 1000); // 1s buckets
          const name = event.toolName || event.tool_name || 'tool';
          toolId = `${name}-${bucket}`;
        }
        
        const toolName = event.toolName || event.tool_name || '';
        
        // Always render the tool header now that we have a deterministic id
        
        // Initialize per-tool dedup set
        try {
          if (toolId) {
            perToolOutputSeenRef.current.set(toolId, new Set());
            while (perToolOutputSeenRef.current.size > MAX_TOOL_DEDUPE_SESSIONS) {
              const oldestKey = perToolOutputSeenRef.current.keys().next().value;
              if (!oldestKey) break;
              perToolOutputSeenRef.current.delete(oldestKey);
            }
          }
        } catch {}
        
        // Reset phase flags
        seenThinkingThisPhaseRef.current = false;
        setCurrentToolId(toolId);
        // Entering a tool phase should end any active reasoning session
        if (activeReasoning) {
          setActiveReasoning(false);
        }
        
        // Always emit the tool event
        results.push({
          type: 'tool_start',
          tool_name: toolName,
          tool_input: event.args || event.tool_input || {},
          toolId: toolId,
          toolName: event.toolName,
          tool_id: toolId,  // Include tool_id for compatibility
          agent_run_id: event.agent_run_id,
          agent_name: event.agent_name,
          agent_type: event.agent_type,
          parent_agent_run_id: event.parent_agent_run_id
        } as DisplayStreamEvent);

        // Show single unified thinking animation
        // Edge case fix: show spinner even if reasoning was active, since we just ended it above.
        if (!activeThinkingRef.current) {
          cancelDelayedThinking();
          
          activateThinking('tool_execution');
          seenThinkingThisPhaseRef.current = true;
        }
        break;
        
      case 'tool_input_update':
        // Handle tool input updates from swarm agents
        // Pass through the event with tool_id and updated input
        results.push({
          type: 'tool_input_update',
          tool_id: event.tool_id || event.toolId,
          tool_input: event.tool_input || event.args || {}
        } as DisplayStreamEvent);
        break;
        
      case 'tool_input_corrected':
        // Handle corrected tool input from backend (e.g., parsed shell commands)
        // This fixes the [object Object] display issue for shell commands
        results.push({
          type: 'tool_input_update',
          tool_id: event.tool_id || event.toolId,
          tool_input: event.tool_input || {}
        } as DisplayStreamEvent);
        break;

      case 'tool_invocation_start':
        // Skip this event - the backend emits both tool_start and tool_invocation_start
        // We only need to process tool_start which has more complete information
        // This prevents duplicate tool displays in the UI
        break;
        
      case 'tool_invocation_end':
        hasSeenOperationActivityRef.current = true;
        const invocationEndAgg = flushAggregatedOutput();
        if (invocationEndAgg) {
          results.push(invocationEndAgg as DisplayStreamEvent);
        }
        // Some backends emit tool_invocation_end without a corresponding tool_end.
        // Ensure we stop any active thinking spinner and reset tool state to avoid "still running" UI.
        cancelPostToolIdleTimer();
        cancelPostReasoningIdleTimer();
        // Mark end of tool streaming
        lastToolOutputTsRef.current = Date.now();
        if (activeThinking) {
          deactivateThinking();
        }
        cancelDelayedThinking();
        seenThinkingThisPhaseRef.current = false;
        setCurrentToolId(undefined);
        // Immediately show a spinner while the agent processes the tool result and prepares reasoning
        if (!activeReasoning) {
          activateThinking('waiting');
        }
        // Optionally, we do not emit a separate tool_end display item here to avoid duplicates
        break;
        
      case 'shell_command':
        // Do not flush pending reasoning here; wait for progress_update to ensure correct attribution
        results.push({
          type: 'shell_command',
          command: event.command,
          toolId: currentToolIdRef.current,
          id: `shell_${Date.now()}`,
          timestamp: new Date().toISOString(),
          sessionId: 'current'
        } as DisplayStreamEvent);
        // Don't add separate animations for shell commands - handled by parent tool
        break;

      case 'command':
        // Generic command event - don't add separate animations
        results.push(event as DisplayStreamEvent);
        break;

      case 'prompt_change':
        emitTestMarker(`prompt_change action=${event.action}`);
        results.push(event as DisplayStreamEvent);
        break;
        
      case 'model_invocation_start':
        hasSeenOperationActivityRef.current = true;
        const modelStartAgg = flushAggregatedOutput();
        if (modelStartAgg) {
          results.push(modelStartAgg as DisplayStreamEvent);
        }
        // When the model is invoked (post-tool), show a spinner immediately to indicate
        // the agent is preparing reasoning. This covers gaps before the first reasoning block.
        cancelDelayedThinking();
        cancelPostToolIdleTimer();
        cancelPostReasoningIdleTimer();
        if (!activeThinkingRef.current) {
          activateThinking('reasoning');
        }
        // Do not render the model event itself; UI shows spinner instead
        break;

      case 'model_stream_delta':
      case 'reasoning_delta':
        // Streaming deltas are handled by StreamDisplay or aggregated elsewhere; don't render here
        break;

      case 'output':
        emitTestMarker('output');
        if ((event as any)?.metadata?.syntheticToolStart) {
          break;
        }
        // Normalize content to detect empty/whitespace-only lines
        const rawOut = (event as any).content != null ? String((event as any).content) : '';
        const isEmptyOut = rawOut.trim().length === 0;
        // Determine whether this output belongs to a tool buffer regardless of content
        const activeToolId = currentToolIdRef.current;
        const fromToolBufferFlag = !!(((event as any)?.metadata?.fromToolBuffer) || ((event as any)?.metadata?.tool) || Boolean(activeToolId));

        // Maintain post-tool bridging behavior even for empty outputs
        if (activeThinking && fromToolBufferFlag) {
          deactivateThinking();
        }

        if (fromToolBufferFlag) {
          // Update last tool output timestamp and start idle timer to show spinner after output
          lastToolOutputTsRef.current = Date.now();
          cancelPostToolIdleTimer();
          postToolIdleTimerRef.current = setTimeout(() => {
            if (!activeThinkingRef.current && !activeReasoning) {
              scheduleDelayedThinking({ delay: 0, context: 'waiting', addSpacer: false });
            }
            // Exit tool phase to avoid misclassifying subsequent non-tool output
            setCurrentToolId(undefined);
            postToolIdleTimerRef.current = null;
          }, 60) as unknown as NodeJS.Timeout;
        } else {
          // If a non-tool output arrives shortly after tool output, bridge with a spinner
          const sinceLastToolMs = Date.now() - (lastToolOutputTsRef.current || 0);
          if (sinceLastToolMs > 0 && sinceLastToolMs < 1500 && !activeThinkingRef.current && !activeReasoning) {
            activateThinking('waiting');
            // Also exit tool phase since we've transitioned to waiting for reasoning
            setCurrentToolId(undefined);
          }
        }

        // Only cancel post-reasoning spinner if we have meaningful output
        if (!isEmptyOut) {
          cancelPostReasoningIdleTimer();
        }

        // If we are still before operation_init, keep a startup spinner visible even as
        // status/output lines arrive. This avoids a dead UI during initial setup.
        if (!operationIdRef.current && !hasSeenOperationActivityRef.current && !activeThinkingRef.current) {
          scheduleDelayedThinking({ delay: 0, context: 'startup', addSpacer: false });
        }

        // Output should not be held; but ensure we don't have stale pending reasoning lingering
        // If output appears, we do NOT auto-flush pending reasoning; it may belong under the next header
        // Handle tool output or general output with deduplication
        if (event.content) {
          // Suppress verbose termination block lines after ESC
          if (suppressTerminationBannerRef.current) {
            const line = String(event.content).trim();
            const isDivider = /^(\[\u2500-\u257F\u2501\u2509\u250A\u250B\u250C\u250D\u250E\u250F\u2510\u2511\u2512\u2513\u2574\u2576\u2501\u2500\-\=\_\~\s\]){10,}$/.test(line) || /\u2501|\u2500|\u2502|\u2503|\u2505|\u2507|\u2509/.test(line);
            const terminationPhrases = [
              'ESC Kill Switch activated',
              'Assessment stopped by user',
              'OPERATION TERMINATED BY USER',
              'Assessment was stopped before completion',
              'You can start a new assessment or review partial results'
            ];
            const isTerminationLine = terminationPhrases.some(p => line.includes(p));
            if (!line || isDivider || isTerminationLine) {
              break; // skip noisy termination lines
            }
          }
          // Enhanced deduplication - check for similar content
          const currentTime = Date.now();
          const contentStr = String(event.content);
          const lastOutputContent = lastOutputContentRef.current;
          const lastOutputTime = lastOutputTimeRef.current;
          
          // Check if this is a duplicate or subset of the last output
          if (lastOutputContent && currentTime - lastOutputTime < OUTPUT_DEDUPE_TIME_MS) {
            // Exact match
            if (contentStr === lastOutputContent) {
              break; // Skip duplicate
            }
            
            // Check if one contains the other (common with Execution Summary vs raw output)
            if (contentStr.includes(lastOutputContent) || lastOutputContent.includes(contentStr)) {
              // Keep the longer/more complete version
              if (contentStr.length <= lastOutputContent.length) {
                break; // Skip this shorter/subset version
              }
            }
          }
          // Fingerprint-based dedup across tool session
          try {
            const fp = fingerprintContent(contentStr);
            let seen = false;
            if (activeToolId) {
              seen = rememberToolOutputFingerprint(activeToolId, fp);
            } else {
              seen = rememberGlobalOutputFingerprint(fp);
            }
            if (seen) {
              break; // skip duplicate chunk/content for this tool/session
            }
          } catch {}

          lastOutputContentRef.current = contentStr.length > 4096
            ? `${contentStr.slice(0, 2048)}\n${contentStr.slice(-2048)}`
            : contentStr;
          lastOutputTimeRef.current = currentTime;
          
          // above we already handled spinner transitions irrespective of content

          // Reorder operation summary vs report preview/content: buffer op-summary and flush after report
          const fromToolBuffer = fromToolBufferFlag;
          const isReportPreview = contentStr.includes('# SECURITY ASSESSMENT REPORT') || contentStr.includes('# CTF Challenge Assessment Report') || contentStr.includes('EXECUTIVE SUMMARY') || contentStr.includes('KEY FINDINGS') || contentStr.includes('REMEDIATION ROADMAP');
          const isOperationSummary = !fromToolBuffer && (
            contentStr.includes('Outputs stored in:') ||
            contentStr.includes('Memory stored in:') ||
            contentStr.includes('Report saved to:') ||
            contentStr.includes('REPORT ALSO SAVED TO:') ||
            contentStr.includes('OPERATION LOGS:') ||
            contentStr.includes('Operation ID:')
          );

          if (isOperationSummary) {
            // Buffer operation summary to show after report preview/content
            opSummaryBufferRef.current.push({
              type: 'output',
              content: event.content,
              toolId: activeToolId
            } as DisplayStreamEvent);
            if (opSummaryBufferRef.current.length > MAX_OPERATION_SUMMARY_EVENTS) {
              opSummaryBufferRef.current = opSummaryBufferRef.current.slice(-MAX_OPERATION_SUMMARY_EVENTS);
            }
            break;
          }

          // Clean placeholder tokens sometimes prefixed in combined output blocks
          let cleanedContent = contentStr;
          if (/^(output|reasoning)(\s*\[[^\]]+\])?\s*\n/i.test(cleanedContent)) {
            cleanedContent = cleanedContent.replace(/^(output|reasoning)(\s*\[[^\]]+\])?\s*\n/i, '');
          }
          // If after cleaning this is a pure placeholder, skip it entirely
          const trimmedClean = cleanedContent.trim();
          if (/^(output|reasoning)(\s*\[[^\]]+\])?$/i.test(trimmedClean)) {
            break;
          }

          // Push normal output
          const outEvt: DisplayStreamEvent = {
            type: 'output',
            content: cleanedContent,
            toolId: activeToolId,
            // Preserve metadata so the renderer can identify tool-buffer outputs
            metadata: {
              ...(event.metadata || {}),
              ...(completionPhaseActiveRef.current ? { finalReportCluster: true } : {}),
            },
          } as DisplayStreamEvent;
          results.push(outEvt);

          // If we're in the FINAL REPORT phase, include this output (typically the
          // ASCII summary banner) in the dynamic final report cluster so it
          // appears directly beneath the inline preview.
          if (completionPhaseActiveRef.current) {
            appendCompletionPhaseEvent(outEvt);
          }

          // If this is a report preview block, immediately flush any buffered operation summary below it
          if (isReportPreview && opSummaryBufferRef.current.length > 0) {
            results.push(...opSummaryBufferRef.current);
            opSummaryBufferRef.current = [];
          }
        } else {
          // For empty/whitespace-only output, we already handled spinner transitions.
          // Skip rendering to avoid blank gaps in the UI.
        }
        break;
        
      case 'tool_end':
        hasSeenOperationActivityRef.current = true;
        const toolEndAgg = flushAggregatedOutput();
        if (toolEndAgg) {
          results.push(toolEndAgg as DisplayStreamEvent);
        }
        cancelPostToolIdleTimer();
        cancelPostReasoningIdleTimer();
        // Clear any active thinking when tool ends
        if (activeThinking) {
          deactivateThinking();
        }
        // Exit tool phase on tool_end
        setCurrentToolId(undefined);
        // Immediately show a short waiting spinner while transitioning to reasoning
        if (!activeReasoning) {
          activateThinking('waiting');
        }
        // Reset flags and cancel pending delayed thinking
        cancelDelayedThinking();
        seenThinkingThisPhaseRef.current = false;

        // Don't flush reasoning here - let it accumulate until progress_update
        // This ensures all reasoning within a step appears as one block

        results.push({
          type: 'tool_end',
          toolId: event.toolId,
          tool: event.toolName || 'unknown',
          agent_run_id: event.agent_run_id,
          agent_name: event.agent_name,
          agent_type: event.agent_type,
          parent_agent_run_id: event.parent_agent_run_id,
          success: event.success,
          outcome: event.outcome,
          executed: event.executed,
          id: `tool_end_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          timestamp: new Date().toISOString(),
          sessionId: 'current'
        } as DisplayStreamEvent);
        // Clear per-tool dedup set when tool ends
        try { if (event.toolId) perToolOutputSeenRef.current.delete(String(event.toolId)); } catch {}
        setCurrentToolId(undefined);
        // Spinner already shown above (lines 1147-1160) - no need for additional delayed spinner
        break;

      case 'operation_complete':
        // Clear any active states
        deactivateThinking();
        // Flush any pending reasoning before we finalize
        flushPendingReasoning(results);
        setActiveReasoning(false);

        // If any op-summary lines are still buffered (no report emitted), flush them now before metrics
        if (opSummaryBufferRef.current.length > 0) {
          results.push(...opSummaryBufferRef.current);
          opSummaryBufferRef.current = [];
        }

        results.push({
          type: 'metrics_update',
          metrics: event.metrics || {},
          duration: event.duration
        } as DisplayStreamEvent);

        // CRITICAL: Aggressive memory cleanup after operation completes
        // Keep only the most recent events to prevent 6GB heap exhaustion
        cancelCompletionCleanupTimer();
        completionCleanupTimerRef.current = setTimeout(() => {
          // Keep only last 100 completed events (enough for final report + metrics)
          const MAX_RETAINED_EVENTS = 100;
          const allCompleted = completedBufRef.current.toArray();

          if (allCompleted.length > MAX_RETAINED_EVENTS) {
            const toKeep = allCompleted.slice(-MAX_RETAINED_EVENTS);
            completedBufRef.current.clear();
            for (const evt of toKeep) {
              completedBufRef.current.push(evt);
            }
            setCompletedEvents(toKeep);
          }

          // Move final active events to completed
          const activeSnapshot = activeBufRef.current.toArray();
          for (const evt of activeSnapshot) {
            completedBufRef.current.push(evt);
          }

          // Clear active buffer completely
          activeBufRef.current.clear();
          setActiveEvents([]);

          // Force garbage collection to release memory immediately
          if (global.gc) {
            try {
              global.gc();
            } catch (e) {
              // GC not available
            }
          }
          completionCleanupTimerRef.current = null;
        }, 1000); // Wait 1s for final renders to complete

        break;
        
      case 'tool_output':
        // Standardized tool output: treat as first tool signal for pending step
        flushPendingProgressUpdate(results);
        // Do not flush pending reasoning here; wait for progress_update to ensure correct attribution
        results.push(event as DisplayStreamEvent);
        break;

        
      case 'report_content':
        // Trim massive report content to prevent OOM and rely on InlineReportViewer for full content.
        flushPendingReasoning(results);
        // Augment event with operation metadata and replace content with a trimmed preview
        const rcEvent: any = { ...event };
        if (typeof rcEvent.content === 'string') {
          rcEvent.content = buildTrimmedReportContent(rcEvent.content);
        } else if (rcEvent.content) {
          try { rcEvent.content = buildTrimmedReportContent(JSON.stringify(rcEvent.content)); } catch { rcEvent.content = ''; }
        }
        if (operationIdRef.current) rcEvent.operation_id = operationIdRef.current;
        if (targetRef.current) rcEvent.target = targetRef.current;
        const displayRcEvent = rcEvent as DisplayStreamEvent;
        results.push(displayRcEvent);
        // If we're inside the completion phase, add this to the dynamic
        // cluster so StreamDisplay can compute reportDetails
        // (path + inline content) for InlineReportViewer.
        if (completionPhaseActiveRef.current) {
          appendCompletionPhaseEvent(displayRcEvent);
        }
        // Synthesize a paths section immediately below the report
        try {
          const opId = operationIdRef.current || '';
          const target = targetRef.current || '';
          const safeTarget = target ? target.replace(/^https?:\/\//, '').replace(/\.{2}|\.\//g, '').replace(/[^a-zA-Z0-9._-]/g, '_').replace(/_+/g, '_').replace(/^[_\.]+|[_\.]+$/g, '') : '';
          const base = safeTarget && opId ? `./outputs/${safeTarget}/${opId}` : '';
          const reportPath = base ? `${base}/security_assessment_report.md` : '';
          const logPath = base ? `${base}/cyber_operations.log` : '';
          const artifactsPath = base ? `${base}/artifacts` : '';
          const pathsEvent: DisplayStreamEvent = {
            type: 'report_paths',
            operation_id: opId,
            target,
            outputDir: base,
            reportPath,
            logPath,
            artifactsPath,
          } as unknown as DisplayStreamEvent;

          results.push(pathsEvent);

          if (completionPhaseActiveRef.current) {
            appendCompletionPhaseEvent(pathsEvent);
          }
        } catch {}
        // Then flush any buffered operation summary (paths) so they appear beneath the report as well
        if (opSummaryBufferRef.current.length > 0) {
          results.push(...opSummaryBufferRef.current);
          opSummaryBufferRef.current = [];
        }
        break;

      case 'evaluation_complete': {
        const evaluationEvent = event as DisplayStreamEvent;
        results.push(evaluationEvent);
        if (completionPhaseActiveRef.current) {
          appendCompletionPhaseEvent(evaluationEvent);
        }
        deactivateThinking();
        break;
      }

      case 'evaluation_step_complete': {
        const evaluationStepEvent = event as DisplayStreamEvent;
        results.push(evaluationStepEvent);
        if (completionPhaseActiveRef.current) {
          appendCompletionPhaseEvent(evaluationStepEvent);
        }
        break;
      }

      case 'assessment_complete': {
        const acEvent = event as DisplayStreamEvent;
        results.push(acEvent);
        if (completionPhaseActiveRef.current) {
          appendCompletionPhaseEvent(acEvent);
          completionPhaseActiveRef.current = false;
        }
        deactivateThinking();
        break;
      }

      case 'rate_limit':
        cancelDelayedThinking();
        activateThinking('rate_limit', `Rate Limit for ${Math.ceil(event.wait_total)}s${event.message ? `: ${event.message}` : ''}`);
        seenThinkingThisPhaseRef.current = true;
        break;

      default:
        // Pass through other events as-is (no synthetic headers)
        results.push(event as DisplayStreamEvent);
        try { lastPushedTypeRef.current = String((event as any).type || ''); } catch {}
        break;
    }
    
    return results;
  };

  useEffect(() => {
    
    // Listen for events from Docker service
    const handleEvent = (rawEvent: any) => {
      const event = normalizeEvent(rawEvent);
      if (event.type === 'progress_update' && formatOperationHealth(event.health)) {
        onHealthUpdate?.(event.health as OperationHealthSnapshot);
      }
      // Debug logging disabled for production use
      // console.error(`[DEBUG] UnconstrainedTerminal received event:`, {
      //   type: event.type,
      //   hasContent: !!event.content,
      //   hasMetrics: !!event.metrics,
      //   timestamp: new Date().toISOString()
      // });
      
      // Preserve preflight and discovery output printed before operation_init.
      // Memory is bounded by CYBER_MAX_EVENTS ring buffer rather than clearing mid-run.

      // Handle metrics updates - backend sends cumulative totals, not deltas
      if (event.type === 'metrics_update' && event.metrics) {
        // Coalesce frequent metrics updates within a short window to reduce render churn
        const nowTs = Date.now();
        if (nowTs - (lastMetricsTsRef.current || 0) < METRICS_COALESCE_MS) {
          // Drop this update; a fresher one will arrive soon
          return;
        }
        lastMetricsTsRef.current = nowTs;
        const newMetrics = {
          // Backend sends cumulative totals, use them directly
          tokens: event.metrics.tokens !== undefined ? event.metrics.tokens : metrics.tokens,
          cost: event.metrics.cost !== undefined ? event.metrics.cost : metrics.cost,
          // Duration and counts can be replaced
          duration: event.metrics.duration || metrics.duration,
          memoryOps: event.metrics.memoryOps !== undefined ? event.metrics.memoryOps : metrics.memoryOps,
          evidence: event.metrics.evidence !== undefined ? event.metrics.evidence : metrics.evidence,
          progressPercent: event.metrics.progressPercent !== undefined ? event.metrics.progressPercent : metrics.progressPercent
        };
        // Emit a test marker for metrics updates to aid PTY-based assertions
        try {
          if (process.env.CYBER_TEST_MODE === 'true') {
            const marker = `[TEST_EVENT] metrics_update tokens=${newMetrics.tokens ?? ''} cost=${newMetrics.cost ?? ''} duration=${newMetrics.duration} memoryOps=${newMetrics.memoryOps} evidence=${newMetrics.evidence}`;
            loggingService.info(marker);
            console.log(marker);
          }
        } catch {}
        setMetrics(newMetrics);
        if (onMetricsUpdate) {
          const now = Date.now();
          const emitNow = now - lastEmitRef.current >= EMIT_INTERVAL_MS;
          if (emitNow) {
            lastEmitRef.current = now;
            // Clear any pending timer since we're emitting now
            if (pendingTimerRef.current) {
              clearTimeout(pendingTimerRef.current);
              pendingTimerRef.current = null;
            }
            onMetricsUpdate({
              tokens: newMetrics.tokens,
              cost: newMetrics.cost,
              duration: newMetrics.duration,
              memoryOps: newMetrics.memoryOps,
              evidence: newMetrics.evidence,
              progressPercent: newMetrics.progressPercent,
            });
          } else {
            // Queue latest metrics and schedule trailing emit
            pendingMetricsRef.current = {
              tokens: newMetrics.tokens,
              cost: newMetrics.cost,
              duration: newMetrics.duration,
              memoryOps: newMetrics.memoryOps,
              evidence: newMetrics.evidence,
              progressPercent: newMetrics.progressPercent,
            };
            if (!pendingTimerRef.current) {
              const delay = EMIT_INTERVAL_MS - (now - lastEmitRef.current);
              pendingTimerRef.current = setTimeout(() => {
                lastEmitRef.current = Date.now();
                const m = pendingMetricsRef.current;
                pendingMetricsRef.current = null;
                pendingTimerRef.current = null;
                if (m) {
                  onMetricsUpdate(m);
                }
              }, Math.max(0, delay));
            }
          }
        }
        if (onEvent) onEvent(event);
        return;
      }
      
      // Process event using direct React state management
      const processedEvents = processEvent(event);
      if (processedEvents.length > 0) {
        const regularEvents: DisplayStreamEvent[] = [];

        let currentAggDisplayEvent: DisplayStreamEvent | null = null;
        for (const processedEvent of processedEvents) {
          if (processedEvent.type === 'delayed_thinking_start') {
            // Use unified scheduler for delayed spinner; include spacing for this path
            const delay = (processedEvent as any).delay || 100;
            scheduleDelayedThinking({
              delay,
              context: (processedEvent as any).context || 'tool_execution',
              toolName: (processedEvent as any).toolName,
              toolCategory: (processedEvent as any).toolCategory,
              addSpacer: true,
            });
            continue;
          }

          if (processedEvent.type === 'thinking_end') {
            // End stream spinner but keep aggregated output visible in active tail.
            deactivateThinking();
            setActiveThrottled(prev => {
              activeBufRef.current.clear();
              const aggEv = buildAggDisplayEvent();
              if (aggEv) activeBufRef.current.push(aggEv);
              return activeBufRef.current.toArray();
            });
            continue;
          }

          // Aggregate tool buffer output fragments per step, but DO NOT aggregate system/status outputs
          if (processedEvent.type === 'output') {
            try {
              const any: any = processedEvent as any;
              // Consider output as tool-buffered if metadata says so OR we have an active toolId
              const isToolBuffer = Boolean(
                !any?.metadata?.aggregated &&
                (any?.metadata?.fromToolBuffer || any?.metadata?.tool || Boolean(currentToolIdRef.current))
              );
              if (isToolBuffer) {
                let contentStr = '';
                if (typeof any.content === 'string') contentStr = any.content;
                else if (any.content) contentStr = JSON.stringify(any.content);
                appendToStepAgg(contentStr);
                currentAggDisplayEvent = buildAggDisplayEvent();
                // Skip pushing this output into completed; we'll show one per step
                continue;
              }
            } catch {}
          }
          
          if (processedEvent.type === 'thinking') {
            activateThinking(
              ((processedEvent as any).context || 'tool_execution') as ThinkingContext,
              (processedEvent as any).message,
              processedEvent
            );
            continue;
          }

          regularEvents.push(processedEvent);
        }

        if (currentAggDisplayEvent) {
          setActiveThrottled(() => {
            activeBufRef.current.clear();
            activeBufRef.current.push(currentAggDisplayEvent as DisplayStreamEvent);
            return activeBufRef.current.toArray();
          });
        }

        if (regularEvents.length > 0) {
          // Before anything else, if a new progress update arrived, flush current aggregated output into completed
          const progressUpdates = regularEvents.filter(e =>
            e.type === 'progress_update' &&
            (e as any).operation_stage !== 'final_report' &&
            (e as any).operation_stage !== 'ragas_evaluation'
          );
          if (progressUpdates.length > 0) {
            const aggEv = buildAggDisplayEvent();
            if (aggEv) {
              completedBufRef.current.push(aggEv as any);
              flushCompletedEventsUpdate();
              resetStepAgg();
            }
            // End any active reasoning state at step boundary
            setActiveReasoning(false);
            cancelDelayedThinking();
            const hasActiveThinking = activeBufRef.current.toArray().some(e => e.type === 'thinking');
            if (!hasActiveThinking) {
              // Clear any live tail from previous step (reasoning/output) to prevent leakage.
              activeBufRef.current.clear();
              setActiveEvents(activeBufRef.current.toArray());
              // Show stream spinner for the new step while waiting for tool/tool args.
              activateThinking('tool_execution', undefined, {}, true);
            }
          }

          // Move non-thinking items to completed (excluding output fragments, separators, dividers handled above)
          const newCompletedEvents = regularEvents.filter(e =>
            e.type !== 'thinking' &&
            !(e.type === 'output' && Boolean((e as any).metadata?.finalReportCluster)) &&
            e.type !== 'separator' &&
            e.type !== 'divider' &&
            !isCompletionPhaseEvent(e)
          );
          if (newCompletedEvents.length > 0) {
            completedBufRef.current.pushMany(newCompletedEvents);
            if (shouldFlushCompletedImmediately(newCompletedEvents)) {
              flushCompletedEventsUpdate();
            } else {
              scheduleCompletedEventsUpdate();
            }
          }
        }
      }
      
      // Forward original event to parent
      if (onEvent) {
        onEvent(event);
      }
    };

    // Early return if no execution service
    if (!executionService) {
      return;
    }

    // Subscribe to events
    executionService.on('event', handleEvent);
    
    // Event flushing no longer needed - events are processed directly
    
    // Handle completion to reset state
    const handleComplete = () => {
      const preserved: DisplayStreamEvent[] = [];
      const aggEv = flushAggregatedOutput();
      if (aggEv) preserved.push(aggEv as any);
      resetAllBuffers(preserved);
    };
    
    executionService.on('complete', handleComplete);
    // Use a stable function reference so we can remove it in cleanup
    const handleStopped = () => {
      suppressTerminationBannerRef.current = true;
      const preserved: DisplayStreamEvent[] = [];
      const aggEv = flushAggregatedOutput();
      if (aggEv) preserved.push(aggEv as any);
      resetAllBuffers(preserved);
    };
    executionService.on('stopped', handleStopped);

    // Cleanup
    return () => {
      // Clean up any delayed thinking timers
      cancelDelayedThinking();
      cancelCompletionCleanupTimer();
      cancelCompletedEventsUpdateTimer();
      cancelPostToolIdleTimer();
      cancelPostReasoningIdleTimer();
      if (pendingTimerRef.current) {
        clearTimeout(pendingTimerRef.current);
        pendingTimerRef.current = null;
      }
      if (activeUpdateTimerRef.current) {
        clearTimeout(activeUpdateTimerRef.current);
        activeUpdateTimerRef.current = null;
      }
      executionService.off('event', handleEvent);
      executionService.off('complete', handleComplete);
      executionService.off('stopped', handleStopped);
    };
  }, [executionService, onEvent, onHealthUpdate, onMetricsUpdate, onThinkingUpdate, sessionId, resetAllBuffers]); // Removed 'metrics' - not used in effect, was causing re-runs on every token update

  if (collapsed) {
    return null;
  }

  const hasCompletionPhaseCluster = completionPhaseEvents != null && completionPhaseEvents.length > 0;

  return (
    <Box flexDirection="column" flexGrow={1}>
      {/* Completed events - rendered via Ink Static so historical output is append-only. */}
      {completedEvents.length > 0 && (
        <StaticStreamDisplay
          key={staticSessionKey}
          events={completedEvents}
          terminalWidth={terminalWidth}
          availableHeight={availableHeight}
        />
      )}

      {/* Completion-phase cluster: rendered dynamically so report and evaluation
          events remain append-only while InlineReportViewer receives late content. */}
      {hasCompletionPhaseCluster && completionPhaseEvents && (
        <StreamDisplay
          events={completionPhaseEvents}
          animationsEnabled={animationsEnabled}
          terminalWidth={terminalWidth}
          availableHeight={availableHeight}
        />
      )}

      {/* Active events with content (reasoning, output, etc.) - suppressed once FINAL
          REPORT cluster takes over the dynamic tail. */}
      {!hasCompletionPhaseCluster && activeEvents.length > 0 && (
        <StreamDisplay
          events={activeEvents}
          animationsEnabled={animationsEnabled}
          terminalWidth={terminalWidth}
          availableHeight={availableHeight}
        />
      )}
    </Box>
  );
});
