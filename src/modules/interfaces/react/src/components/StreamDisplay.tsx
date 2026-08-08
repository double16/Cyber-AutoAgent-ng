/**
 * StreamDisplay - SDK-integrated event streaming for cyber operations
 * Designed for infinite scroll with SDK native events and backward compatibility
 */

import React from 'react';
import { Box, Static, Text } from 'ink';
import { StreamEvent } from '../types/events.js';
import { formatToolInput } from '../utils/toolFormatters.js';
import { DISPLAY_LIMITS } from '../constants/config.js';
import { themeManager } from '../themes/theme-manager.js';
// Removed toolCategories import - using clean tool display without emojis
import * as fs from 'fs/promises';
import * as fsSync from 'fs';
import * as path from 'path';
import stripAnsi from 'strip-ansi';
import { useConfig } from '../contexts/ConfigContext.js';
import type { Config } from '../contexts/ConfigContext.js';
import { formatOperationHealth } from '../utils/operationHealthFormatting.js';
import type { OperationHealthSnapshot } from '../utils/operationHealthFormatting.js';
import { formatWorkflowActivityEvent } from '../utils/workflowActivityFormatting.js';
import { MarkdownRenderer } from '../utils/markdownRows.js';

const PROJECT_MARKERS = ['pyproject.toml', path.join('docker', 'docker-compose.yml'), '.git'];
let cachedProjectRoot: string | null | undefined;

const resolveProjectRoot = (): string | null => {
  if (cachedProjectRoot !== undefined) {
    return cachedProjectRoot;
  }
  let currentDir = process.cwd();
  for (let depth = 0; depth < 10; depth += 1) {
    const hasMarker = PROJECT_MARKERS.some(marker => {
      try {
        return fsSync.existsSync(path.join(currentDir, marker));
      } catch {
        return false;
      }
    });
    if (hasMarker) {
      cachedProjectRoot = currentDir;
      return currentDir;
    }
    const parent = path.dirname(currentDir);
    if (parent === currentDir) {
      break;
    }
    currentDir = parent;
  }
  cachedProjectRoot = null;
  return null;
};

// Extended event types for UI-specific events not covered by the core SDK events
// These events are used for UI state management and display formatting
export type AdditionalStreamEvent = 
  | {
      type: 'progress_update';
      step: number | string;
      progressPercent?: number;
      totalTools?: number;
      operation?: string;
      duration?: string;
      health?: OperationHealthSnapshot;
      [key: string]: any;
    }
  | { type: 'evaluation_step_complete'; status: 'completed' | 'skipped' | 'failed'; evaluation_step_kind: string; [key: string]: any }
  | { type: 'evaluation_complete'; status?: 'completed' | 'no_results' | 'failed'; scores?: Record<string, number>; [key: string]: any }
  | { type: 'reasoning'; content: string; [key: string]: any }
  | { type: 'thinking'; context?: 'reasoning' | 'tool_preparation' | 'tool_execution' | 'waiting' | 'startup' | 'rate_limit'; startTime?: number; urgent?: boolean; [key: string]: any }
  | { type: 'thinking_end'; [key: string]: any }
  | { type: 'delayed_thinking_start'; context?: string; startTime?: number; delay?: number; [key: string]: any }
  | { type: 'tool_start'; tool_name: string; tool_input: any; [key: string]: any }
  | { type: 'tool_input_update'; tool_id: string; tool_input: any; [key: string]: any }
  | { type: 'tool_input_corrected'; tool_id: string; tool_input: any; [key: string]: any }
  | { type: 'command'; content: string; [key: string]: any }
  | { type: 'output'; content: string; exitCode?: number; duration?: number; [key: string]: any }
  | { type: 'tool_discovery_start'; message?: string; [key: string]: any }
  | { type: 'tool_available'; tool_name?: string; description?: string; [key: string]: any }
  | { type: 'tool_unavailable'; tool_name?: string; description?: string; [key: string]: any }
  | { type: 'environment_ready'; message?: string; tool_count?: number; [key: string]: any }
  | { type: 'error'; content: string; [key: string]: any }
  | { type: 'metadata'; content: Record<string, string>; [key: string]: any }
  | { type: 'divider'; [key: string]: any }
  | { type: 'separator'; content?: string; [key: string]: any }
  | { type: 'user_handoff'; message: string; breakout: boolean; [key: string]: any }
  | { type: 'metrics_update'; metrics: any; [key: string]: any }
  | { type: 'model_invocation_start'; modelId?: string; [key: string]: any }
  | { type: 'model_stream_delta'; delta?: string; [key: string]: any }
  | { type: 'reasoning_delta'; delta?: string; [key: string]: any }
  | { type: 'tool_invocation_start'; toolName?: string; toolInput?: any; [key: string]: any }
  | { type: 'tool_invocation_end'; duration?: number; success?: boolean; [key: string]: any }
  | { type: 'event_loop_cycle_start'; cycleNumber?: number; [key: string]: any }
  | { type: 'content_block_delta'; delta?: string; isReasoning?: boolean; [key: string]: any }
  | { type: 'specialist_start'; specialist?: string; task?: string; finding?: string; artifactPaths?: string[]; [key: string]: any }
  | { type: 'specialist_progress'; specialist?: string; gate?: number; totalGates?: number; tool?: string; status?: string; [key: string]: any }
  | { type: 'specialist_end'; specialist?: string; result?: any; [key: string]: any }
  | { type: 'batch'; id?: string; events: DisplayStreamEvent[]; [key: string]: any }
  | { type: 'tool_output'; tool: string; status?: string; output?: any; [key: string]: any }
  | { type: 'operation_init'; operation_id?: string; target?: string; objective?: string; memory?: any; [key: string]: any }
  | { type: 'operation_terminated'; termination_reason?: string; completion_status?: any; workflow_coverage_summary?: any[]; model_usage_snapshot?: any; [key: string]: any }
  | { type: 'operation_finalized'; termination_reason?: string; report_status?: string; report_path?: string; evaluation_status?: string; [key: string]: any }
  | { type: 'report_paths'; operation_id?: string; target?: string; outputDir?: string; reportPath?: string; logPath?: string; artifactsPath?: string; [key: string]: any }
  | { type: 'workflow_activity'; content?: string; activity?: string; action?: string; role?: string; status?: string; phase_id?: number; phase_title?: string; task_uid?: string; task_title?: string; attempt?: number; attempt_total?: number; cycle?: number; cycle_total?: number; iteration?: number; iteration_total?: number; [key: string]: any }
  | { type: 'preflight_check'; operation_id?: string; target_id?: string; target?: string; target_type?: string; status: 'pass' | 'fail' | 'skip'; checks?: string[]; reason?: string; resolved_addresses?: string[]; [key: string]: any }
  | { type: 'task_started'; task_uid?: string; title?: string; status?: string; task_kind?: string; reference_id?: string; [key: string]: any }
  | { type: 'task_done'; task_uid?: string; title?: string; status?: string; status_reason?: string; task_kind?: string; reference_id?: string; finding_resolution?: string; [key: string]: any }
  | { type: 'task_deferred'; task_uid?: string; title?: string; status?: string; status_reason?: string; task_kind?: string; reference_id?: string; [key: string]: any }
  | { type: 'rate_limit'; sleep_time?: number; wait_total?: number; message?: string; [key: string]: any };

// Combined event type supporting both SDK-aligned and additional events
export type DisplayStreamEvent = StreamEvent | AdditionalStreamEvent;

// Re-export StreamEvent type for backward compatibility
export type { StreamEvent };

const formatTaskScope = (event: any): string => {
  const scope = typeof event?.target_scope === 'string' ? event.target_scope.trim() : '';
  const ids = Array.isArray(event?.target_ids) ? event.target_ids.filter(Boolean).join(',') : '';
  if (!scope || scope === 'all') return '';
  return ids ? ` [scope: ${ids}]` : ` [scope: ${scope}]`;
};

const flattenDisplayEvents = (events: DisplayStreamEvent[]): DisplayStreamEvent[] => events.flatMap(event =>
  event.type === 'batch' && Array.isArray(event.events) ? flattenDisplayEvents(event.events) : [event]
);

type TerminationDetail = { reason?: string; message?: string };

const latestTerminationDetail = (events: DisplayStreamEvent[]): TerminationDetail | null => {
  const termination = [...flattenDisplayEvents(events)]
    .reverse()
    .find(event => event.type === 'termination_reason');
  if (!termination) return null;
  const reason = typeof (termination as any).reason === 'string'
    ? (termination as any).reason.trim()
    : '';
  const message = typeof (termination as any).message === 'string'
    ? (termination as any).message.trim()
    : '';
  return reason || message ? { reason: reason || undefined, message: message || undefined } : null;
};

interface StreamDisplayProps {
  events: DisplayStreamEvent[];
  // Configuration for SDK features
  showSDKMetrics?: boolean;
  showPerformanceInfo?: boolean;
  enableCostTracking?: boolean;
  animationsEnabled?: boolean;
  // Terminal dimensions for layout calculations
  terminalWidth?: number;
  availableHeight?: number;
}

// Compute divider dynamically to avoid stale or zero-width when terminal resizes
const getDivider = (): string => {
  try {
    const cols = (process.stdout && (process.stdout as any).columns) ? (process.stdout as any).columns : 80;
    // Ensure a sensible minimum so the line is visible even in constrained environments
    const width = Math.max(60, Number(cols) || 80);
    return '─'.repeat(width);
  } catch {
    return '─'.repeat(80);
  }
};

// Operation context used to locate artifacts like the final report
type OperationContext = {
  operationId?: string | null;
  target?: string | null;
  reportPath?: string | null;
};

// Utility: sanitize target into safe path segment (mirrors Python logic)
const sanitizeTargetForPath = (target: string): string => {
  try {
    let clean = target.replace(/^https?:\/\//, '');
    clean = clean.replace(/\.\./g, '').replace(/\.\//g, '');
    clean = clean.replace(/[^a-zA-Z0-9._-]/g, '_');
    clean = clean.replace(/_+/g, '_');
    clean = clean.slice(0, 100).replace(/^[_\.]+|[_\.]+$/g, '');
    return clean || 'unknown_target';
  } catch {
    return 'unknown_target';
  }
};

// Helper: map container-relative report paths (e.g., /app/outputs/...) to host outputDir
// This is exported for unit tests and to keep behavior consistent anywhere we
// need to interpret backend report paths.
export const mapContainerReportPath = (
  raw: string,
  outputBaseDir?: string | null
): string => {
  try {
    let normalized = raw;
    if (normalized.startsWith('/app/outputs') && outputBaseDir) {
      const suffix = normalized.replace('/app/outputs', '');
      normalized = path.join(outputBaseDir, suffix.replace(/^\/+/, ''));
    }
    return normalized;
  } catch {
    return raw;
  }
};

type ReportDetails = {
  path: string | null;
  content: string | null;
};

export const deriveReportDetails = (
  events: DisplayStreamEvent[],
  outputBaseDir?: string | null
): ReportDetails => {
  let latestPath: string | null = null;
  let fallback: string | null = null;

  events.forEach(event => {
    if (event.type === 'report_paths') {
      const candidate =
        (event as any).reportPath ??
        (event as any).report_path ??
        (event as any).report ??
        null;
      if (candidate) {
        latestPath = mapContainerReportPath(String(candidate), outputBaseDir);
      }
    } else if ((event as any).type === 'assessment_complete') {
      const raw = (event as any).report_path;
      if (typeof raw === 'string' && raw) {
        latestPath = mapContainerReportPath(raw, outputBaseDir);
      }
    } else if (event.type === 'report_content') {
      if ('content' in event && typeof (event as any).content === 'string') {
        fallback = (event as any).content;
      } else if ('content' in event && (event as any).content) {
        try {
          fallback = JSON.stringify((event as any).content);
        } catch {
          fallback = String((event as any).content);
        }
      }
    }
  });

  return { path: latestPath, content: fallback };
};

export const deriveOperationContext = (
  events: DisplayStreamEvent[],
  reportPath: string | null
): OperationContext => {
  let operationId: string | null = null;
  let target: string | null = null;

  events.forEach(event => {
    if (event.type === 'operation_init') {
      if ('operation_id' in event && (event as any).operation_id) {
        operationId = String((event as any).operation_id);
      }
      if ('target' in event && (event as any).target) {
        target = String((event as any).target);
      }
    }
  });

  return { operationId, target, reportPath };
};

export const getReportPathCandidates = (
  ctx: OperationContext,
  reportPath: string | null | undefined,
  projectRoot: string | null | undefined,
  outputBaseDir?: string | null | undefined
): string[] => {
  const candidates: string[] = [];

  const addCandidate = (candidate?: string | null) => {
    if (!candidate) return;
    const normalized = path.normalize(candidate);
    if (!candidates.includes(normalized)) {
      candidates.push(normalized);
    }
  };

  // Determine a sensible base directory for relative paths
  const baseDir = (() => {
    try {
      if (outputBaseDir && typeof outputBaseDir === 'string') {
        return outputBaseDir;
      }
      if (projectRoot) {
        return projectRoot;
      }
      return process.cwd();
    } catch {
      return process.cwd();
    }
  })();

  if (reportPath) {
    try {
      if (path.isAbsolute(reportPath)) {
        addCandidate(reportPath);
      } else {
        addCandidate(path.resolve(baseDir, reportPath));
        if (projectRoot && baseDir !== projectRoot) {
          addCandidate(path.resolve(projectRoot, reportPath));
        }
      }
    } catch {
      // Ignore path resolution errors; we'll fall back to inferred paths below
    }
  }

  // Fallback: infer standard unified output path when we know operationId+target
  if (ctx.operationId && ctx.target) {
    const safeTarget = sanitizeTargetForPath(String(ctx.target));
    const relativePath = path.join(
      safeTarget,
      String(ctx.operationId),
      'security_assessment_report.md'
    );

    try {
      addCandidate(path.resolve(baseDir, relativePath));
      if (projectRoot && baseDir !== projectRoot) {
        addCandidate(path.resolve(projectRoot, path.join('outputs', relativePath)));
      }
      // Historical fallback: repo-root/outputs/<target>/<op>/...
      addCandidate(path.resolve(process.cwd(), path.join('outputs', relativePath)));
    } catch {
      // Best-effort only
    }
  }

  return candidates;
};

const REPORT_FILE_PREVIEW_BYTES = Math.max(
  16384,
  DISPLAY_LIMITS.REPORT_CONTENT_MAX_TOTAL_CHARS || 30000
);

export const readReportPreviewFile = async (
  filePath: string,
  maxBytes = REPORT_FILE_PREVIEW_BYTES
): Promise<string> => {
  const byteBudget = Math.max(1024, Math.floor(maxBytes));
  const handle = await fs.open(filePath, 'r');
  try {
    const stats = await handle.stat();
    const size = Number(stats.size) || 0;
    if (size <= 0) {
      return '';
    }

    if (size <= byteBudget) {
      const buffer = Buffer.alloc(size);
      const { bytesRead } = await handle.read(buffer, 0, size, 0);
      return buffer.subarray(0, bytesRead).toString('utf-8');
    }

    const headBytes = Math.max(1, Math.floor(byteBudget * 0.7));
    const tailBytes = Math.max(1, byteBudget - headBytes);
    const head = Buffer.alloc(headBytes);
    const tail = Buffer.alloc(tailBytes);
    const { bytesRead: headRead } = await handle.read(head, 0, headBytes, 0);
    const { bytesRead: tailRead } = await handle.read(tail, 0, tailBytes, Math.max(0, size - tailBytes));
    const omitted = Math.max(0, size - headRead - tailRead);
    return [
      head.subarray(0, headRead).toString('utf-8'),
      '',
      `... (report file preview truncated; ${omitted} bytes omitted) ...`,
      '',
      tail.subarray(0, tailRead).toString('utf-8'),
    ].join('\n');
  } finally {
    await handle.close();
  }
};

// Inline viewer to load and render the generated markdown report from disk
const InlineReportViewer: React.FC<{
  ctx: OperationContext;
  reportPath?: string | null;
  fallbackContent?: string | null;
  projectRoot?: string | null;
}>= ({ ctx, reportPath, fallbackContent, projectRoot }) => {
  // Seed content directly from fallbackContent so initial render can show a
  // preview even before any file I/O or effects run. The effect below will
  // later upgrade this to the full file contents when available.
  const [content, setContent] = React.useState<string | null>(() => fallbackContent ?? null);
  const [error, setError] = React.useState<string | null>(null);
  const [resolvedPath, setResolvedPath] = React.useState<string | null>(null);

  // Resolve output base directory locally so this component works in isolation
  const { config } = useConfig();
  const outputBaseDir = React.useMemo(() => {
    try {
      const raw = config.outputDir || './outputs';
      if (path.isAbsolute(raw)) {
        return raw;
      }
      const base = resolveProjectRoot() ?? process.cwd();
      return path.resolve(base, raw);
    } catch {
      return path.resolve(process.cwd(), 'outputs');
    }
  }, [config.outputDir]);

  const candidatePaths = React.useMemo(
    () => getReportPathCandidates(ctx, reportPath ?? null, projectRoot ?? null, outputBaseDir ?? null),
    [ctx.operationId, ctx.target, reportPath, projectRoot, outputBaseDir]
  );

  React.useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        if (!cancelled) {
          setError(null);
          setResolvedPath(null);
        }

        const candidates = candidatePaths;

        if (candidates.length === 0) {
          if (!cancelled && !fallbackContent) {
            setError('Report context unavailable');
          }
          return;
        }

        let loaded: string | null = null;
        let usedPath: string | null = null;
        for (const candidate of candidates) {
          try {
            if (!fsSync.existsSync(candidate)) {
              continue;
            }
            loaded = await readReportPreviewFile(candidate);
            usedPath = candidate;
            break;
          } catch {}
        }

        if (!loaded) {
          // We already populated content from fallbackContent above (if present).
          // If there is no fallbackContent, surface a clear error.
          if (!cancelled && !fallbackContent) {
            setError('Report file not found');
          }
          // Prefer the first existing candidate for the "Report saved to" hint.
          try {
            const firstExisting = candidates.find(p => fsSync.existsSync(p));
            if (cancelled) {
              return;
            }
            if (firstExisting) {
              setResolvedPath(firstExisting);
            } else if (reportPath && path.isAbsolute(reportPath)) {
              setResolvedPath(reportPath);
            }
          } catch {
            if (!cancelled && reportPath && path.isAbsolute(reportPath)) {
              setResolvedPath(reportPath);
            }
          }
          return;
        }

        // File read succeeded; prefer bounded file preview over inline fallback.
        if (cancelled) {
          return;
        }
        setContent(loaded);
        if (usedPath) {
          setResolvedPath(usedPath);
        } else if (reportPath && path.isAbsolute(reportPath)) {
          setResolvedPath(reportPath);
        }
      } catch (e: any) {
        // On unexpected errors, keep any existing content (seeded above) and
        // only surface an error if we had nothing to show.
        if (!cancelled && !fallbackContent) {
          setError('Failed to load report');
        }
        try {
          const firstExisting = candidatePaths.find(p => fsSync.existsSync(p));
          if (cancelled) {
            return;
          }
          if (firstExisting) {
            setResolvedPath(firstExisting);
          } else if (reportPath && path.isAbsolute(reportPath)) {
            setResolvedPath(reportPath);
          }
        } catch {
          if (!cancelled && reportPath && path.isAbsolute(reportPath)) {
            setResolvedPath(reportPath);
          }
        }
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [candidatePaths, fallbackContent, reportPath]);

  if (error) {
    return (
      <Box flexDirection="column" marginTop={1}>
        <Text color="yellow">{error}</Text>
        {(resolvedPath || reportPath) && (
          <Box marginTop={1}>
            <Text dimColor>
              Report saved to: {resolvedPath || reportPath}
            </Text>
          </Box>
        )}
      </Box>
    );
  }
  if (!content) {
    return (
      <Box marginTop={1}>
        <Text dimColor>Loading final report…</Text>
        {(resolvedPath || reportPath) && (
          <Box marginTop={1}>
            <Text dimColor>
              Report saved to: {resolvedPath || reportPath}
            </Text>
          </Box>
        )}
      </Box>
    );
  }

  const lines = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  const displayLines: string[] =
    lines.length > DISPLAY_LIMITS.REPORT_PREVIEW_LINES + DISPLAY_LIMITS.REPORT_TAIL_LINES
      ? [
          ...lines.slice(0, DISPLAY_LIMITS.REPORT_PREVIEW_LINES),
          '',
          '... (content continues)',
          '',
          ...lines.slice(-DISPLAY_LIMITS.REPORT_TAIL_LINES),
        ]
      : lines;

  const MAX_PREVIEW_LINE_LENGTH = 320;
  const MAX_INLINE_LINES = 160;
  const MAX_INLINE_CHARS = 12000;

  const previewLines = displayLines.slice(0, MAX_INLINE_LINES).map(line => {
    if (line.length > MAX_PREVIEW_LINE_LENGTH) {
      return `${line.slice(0, MAX_PREVIEW_LINE_LENGTH)}…`;
    }
    return line;
  });

  const totalChars = previewLines.reduce((acc, line) => acc + line.length, 0);

  if (totalChars > MAX_INLINE_CHARS) {
    while (previewLines.length > 0 && previewLines.reduce((acc, line) => acc + line.length, 0) > MAX_INLINE_CHARS) {
      previewLines.splice(previewLines.length - 1, 1);
    }
  }

  const truncatedNotice = displayLines.length > previewLines.length || lines.length > DISPLAY_LIMITS.REPORT_PREVIEW_LINES + DISPLAY_LIMITS.REPORT_TAIL_LINES;

  if (previewLines.length === 0) {
    return (
      <Box flexDirection="column" marginTop={1} marginBottom={1}>
        <Box borderStyle="round" borderColor="yellow" paddingX={1}>
          <Text color="yellow" bold>Report preview truncated</Text>
        </Box>
        <Box flexDirection="column" marginTop={1} paddingX={1}>
          <Text dimColor>
            Inline preview suppressed to avoid overwhelming the terminal renderer. Open the saved
            markdown report for the full content.
          </Text>
          {reportPath ? (
            <Text dimColor>Report path: {reportPath}</Text>
          ) : candidatePaths.length > 0 ? (
            <Text dimColor>Report path: {candidatePaths[0]}</Text>
          ) : null}
        </Box>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" marginTop={1} marginBottom={1}>
      <Text color="cyan" bold>SECURITY ASSESSMENT REPORT</Text>
      <Box flexDirection="column" marginTop={1} paddingX={1}>
        <MarkdownRenderer content={previewLines.join('\n')} />
        {truncatedNotice && (
          <Box marginTop={1}>
            <Text dimColor>
              Preview truncated for brevity. Open the saved markdown report for full content.
            </Text>
          </Box>
        )}
        {(resolvedPath || reportPath) && (
          <Box marginTop={1}>
            <Text dimColor>
              Report saved to: {resolvedPath || reportPath}
            </Text>
          </Box>
        )}
      </Box>
    </Box>
  );
};

// Export EventLine for potential reuse in other components
interface EventLineProps {
  event: DisplayStreamEvent;
  toolInputs?: Map<string, any>;
  animationsEnabled?: boolean;
  operationContext?: OperationContext;
  reportPath?: string | null;
  reportFallbackContent?: string | null;
  projectRoot?: string | null;
  configOverride?: Config;
  // When false, suppresses the InlineReportViewer even if this is the FINAL REPORT
  // header. This is used by StaticStreamDisplay so that the inline preview is
  // rendered only in the dynamic StreamDisplay (which can react to late-arriving
  // report_content and assessment_complete events).
  enableInlineReportView?: boolean;
  terminationDetail?: TerminationDetail | null;
}

export const EventLine: React.FC<EventLineProps> = React.memo(({
  event,
  toolInputs,
  animationsEnabled = true,
  operationContext,
  reportPath,
  reportFallbackContent,
  projectRoot,
  configOverride,
  enableInlineReportView = true,
  terminationDetail,
}) => {
  const { config } = useConfig();
  const effectiveConfig = configOverride ?? config;

  switch (event.type) {
    case 'tool_discovery_start': {
      const message = typeof event.message === 'string' && event.message.trim()
        ? event.message.trim()
        : 'Loading cybersecurity assessment tools';
      return (
        <Box flexDirection="column" marginTop={1}>
          <Text color="cyan" bold>TOOL DISCOVERY</Text>
          <Box marginLeft={2}>
            <Text>🔎 {message}</Text>
          </Box>
        </Box>
      );
    }

    case 'tool_available': {
      const toolName = typeof event.tool_name === 'string' && event.tool_name.trim()
        ? event.tool_name.trim()
        : 'unnamed tool';
      const description = typeof event.description === 'string' ? event.description.trim() : '';
      return (
        <Box marginLeft={2}>
          <Text color="green">🔧 </Text>
          <Text bold>{toolName}</Text>
          {description ? <Text dimColor>{` — ${description}`}</Text> : null}
        </Box>
      );
    }

    case 'tool_unavailable': {
      const toolName = typeof event.tool_name === 'string' && event.tool_name.trim()
        ? event.tool_name.trim()
        : 'unnamed tool';
      const description = typeof event.description === 'string' ? event.description.trim() : '';
      return (
        <Box marginLeft={2}>
          <Text color="yellow">⛔ </Text>
          <Text color="yellow" bold>{toolName}</Text>
          {description ? <Text dimColor>{` — ${description}`}</Text> : null}
          <Text dimColor> (unavailable)</Text>
        </Box>
      );
    }

    case 'environment_ready': {
      const toolCount = Number(event.tool_count);
      const message = typeof event.message === 'string' && event.message.trim()
        ? event.message.trim()
        : Number.isFinite(toolCount)
          ? `Environment ready - ${toolCount} cybersecurity tools loaded`
          : 'Environment ready';
      return (
        <Box marginTop={1}>
          <Text color="green" bold>🟢 {message}</Text>
        </Box>
      );
    }

    // =======================================================================
    // SDK NATIVE EVENT HANDLERS - Integrated with SDK context
    // =======================================================================
    case 'model_invocation_start':
      return (
        <>
          <Text color="blue" bold>model invocation started</Text>
          {'modelId' in event && event.modelId ? (
            <Text dimColor>Model: {event.modelId}</Text>
          ) : null}
          <Text> </Text>
        </>
      );
      
    case 'model_stream_delta':
      return (
        <>
          {'delta' in event && event.delta ? (
            <Text>{event.delta}</Text>
          ) : null}
        </>
      );
      
    case 'reasoning_delta':
      // Don't display reasoning_delta directly - let the aggregator handle it
      return null;
      
    case 'tool_invocation_start':
      // Skip rendering here - we handle tool display in 'tool_start' event
      // This prevents duplicate tool displays
      return null;
      
    case 'tool_invocation_end':
      // Don't show "tool completed" - just let the output speak for itself
      return null;
      
    case 'event_loop_cycle_start':
      return (
        <>
          <Text color="blue" bold>
            [CYCLE {'cycleNumber' in event ? event.cycleNumber : '?'}] Event loop cycle started
          </Text>
          <Text> </Text>
        </>
      );
      
    case 'metrics_update':
      // Do not render metrics inline; Footer displays tokens/cost/duration.
      return null;
      
    case 'content_block_delta':
      return (
        <>
          {'delta' in event && event.delta ? (
            <Text>{'isReasoning' in event && event.isReasoning ? 
              <Text color="cyan">{event.delta}</Text> : 
              <Text>{event.delta}</Text>
            }</Text>
          ) : null}
        </>
      );
      
    // =======================================================================
    // EVENT BOUNDARY HANDLERS
    // =======================================================================
    case 'progress_update':
      let stepDisplay = '';
      const healthVisual = formatOperationHealth((event as any).health);

      const eventAgent = (event as any)['agent_name'];
      const agentSubStep = (event as any)['agent_sub_step'];
      const operationStage = (event as any)['operation_stage'];
      
      if (operationStage === 'ragas_evaluation') {
        const evaluationIndex = Number((event as any)['evaluation_step_index']);
        const evaluationTotal = Number((event as any)['evaluation_step_total']);
        const evaluationKind = String((event as any)['evaluation_step_kind'] || '');
        const evaluationLabel = String((event as any)['evaluation_step_label'] || '').trim();
        const progressLabel = Number.isFinite(evaluationIndex) && Number.isFinite(evaluationTotal)
          ? `${evaluationIndex}/${evaluationTotal}`
          : (evaluationKind === 'reference_topics' ? 'PREPARING' : 'METRIC');
        stepDisplay = `[RAGAS EVALUATION ${progressLabel}]${evaluationLabel ? ` ${evaluationLabel}` : ''}`;
      } else if (operationStage === 'final_report') {
        const reportIndex = Number((event as any)['report_step_index']);
        const reportTotal = Number((event as any)['report_step_total']);
        const reportLabel = String((event as any)['report_step_label'] || '').trim();
        const reportKind = String((event as any)['report_step_kind'] || '').trim();
        const progressLabel = Number.isFinite(reportIndex) && Number.isFinite(reportTotal)
          ? `${reportIndex}/${reportTotal}`
          : 'REPORT';
        const kindLabel = reportKind === 'validation_failure' ? ' [REQUIRES VALIDATION]' : '';
        stepDisplay = `[FINAL REPORT ${progressLabel}]${kindLabel}${reportLabel ? ` ${reportLabel}` : ''}`;
      } else if (event.step === "FINAL REPORT") {
        stepDisplay = "[FINAL REPORT]";
      } else if (typeof event.step === 'string' && String(event.step).toUpperCase() === 'TERMINATED') {
        // Pair the progress header with the later termination payload so failures are visible
        // even when the terminal detail is rendered separately in the event stream.
        const reason = terminationDetail?.reason;
        const message = terminationDetail?.message;
        stepDisplay = `[TERMINATED${reason ? `: ${reason}` : ''}]${message ? ` ${message}` : ''}`;
      } else if (eventAgent && agentSubStep) {
        const agentName = String(eventAgent).toUpperCase().replaceAll('_', ' ');
        const agentTotal = (event as any)['agent_total_actions'] ?? agentSubStep;
        const eventProgress = (event as any).progressPercent;
        const progress = typeof eventProgress === 'number' ? ` | BUDGET ${eventProgress}%` : '';
        stepDisplay = `[AGENT: ${agentName} • ACTION ${agentSubStep} | TOTAL ${agentTotal}${progress}]`;
      } else {
        // Regular progress header with tool count for budget transparency
        const toolCount = (event as any)['totalTools'];
        const eventProgress = (event as any).progressPercent;
        const progress = typeof eventProgress === 'number' ? `${eventProgress}%` : String(event.step || '');
        if (toolCount && toolCount > 0) {
          const label = typeof eventProgress === 'number' ? 'BUDGET' : 'PROGRESS';
          stepDisplay = `[${label} ${progress} | ${toolCount} tools]`;
        } else {
          stepDisplay = typeof eventProgress === 'number'
            ? `[BUDGET ${progress}]`
            : `[PROGRESS ${progress}]`;
        }
      }
      
      return (
        <Box flexDirection="column" marginTop={1}>
          <Box flexDirection="row" alignItems="center">
            <Text color="#89B4FA" bold>
              {stepDisplay}
            </Text>
            {healthVisual && (
              <Text color={healthVisual.color} bold>{` ${healthVisual.label}`}</Text>
            )}
          </Box>
          <Text color="#45475A">{getDivider()}</Text>
          {/* If this is the FINAL REPORT and we have operation context, render the report inline.
              We only do this when enableInlineReportView is true and we already have either a
              resolved reportPath or fallbackContent. This avoids mounting the viewer too early
              (before report_content / assessment_complete arrive), which previously led to
              stale "Loading final report…" states. */}
          {enableInlineReportView &&
            event.step === 'FINAL REPORT' &&
            operationContext &&
            (reportPath || reportFallbackContent) && (
              <InlineReportViewer
                ctx={{
                  ...operationContext,
                  reportPath: reportPath ?? operationContext.reportPath ?? null,
                }}
                reportPath={reportPath ?? operationContext.reportPath ?? null}
                fallbackContent={reportFallbackContent ?? null}
                projectRoot={projectRoot ?? null}
              />
          )}
        </Box>
      );
      
    case 'task_started': {
      if (String((event as any).task_kind || '') !== 'finding_validation') {
        return null;
      }
      const title = String((event as any).title || 'Finding').replace(/^Verify finding:\s*/i, '').trim();
      return (
        <Box>
          <Text color="yellow">{`VERIFYING FINDING ${title}${formatTaskScope(event)}`}</Text>
        </Box>
      );
    }

    case 'preflight_check': {
      const status = String((event as any).status || 'skip').toLowerCase();
      const target = String((event as any).target || 'unknown target');
      const targetType = String((event as any).target_type || 'unknown');
      const checks = Array.isArray((event as any).checks) ? (event as any).checks.join(', ') : '';
      const reason = String((event as any).reason || '').trim();
      const icon = status === 'pass' ? '✓' : status === 'fail' ? '✗' : '■';
      const color = status === 'pass' ? 'green' : status === 'fail' ? 'red' : 'yellow';
      const checkSuffix = checks ? ` [${checks}]` : '';
      const reasonSuffix = reason ? `: ${reason}` : '';
      return (
        <Box>
          <Text color={color}>
            {`${icon} 🎯 ${status.toUpperCase()} ${target} (${targetType})${checkSuffix}${reasonSuffix}`}
          </Text>
        </Box>
      );
    }

    case 'workflow_activity': {
      const status = String((event as any).status || 'started').toLowerCase();
      const formattedActivity = formatWorkflowActivityEvent(event);
      const color = ['completed', 'success', 'done'].includes(status)
        ? 'green'
        : ['failed', 'error', 'blocked', 'partial_failure', 'cancelled', 'canceled', 'skipped', 'terminated', 'not_applicable'].includes(status)
          ? 'yellow'
          : 'cyan';
      return (
        <Box>
          <Text color={color}>
            {formattedActivity || ''}
          </Text>
        </Box>
      );
    }

    case 'task_done': {
      const title = String((event as any).title || '').trim();
      const status = String((event as any).status || 'done').trim().toLowerCase();
      const statusReason = String((event as any).status_reason || '').trim();
      const findingResolution = String((event as any).finding_resolution || '').trim();
      if (findingResolution) {
        const findingTitle = title.replace(/^Verify finding:\s*/i, '').trim();
        const verified = findingResolution === 'verified';
        const label = verified ? 'FINDING VERIFIED' : 'FINDING REQUIRES VALIDATION';
        const detail = statusReason ? `: ${statusReason}` : '';
        return (
          <Box>
            <Text color={verified ? 'green' : 'yellow'}>
              {`${label}${findingTitle ? ` ${findingTitle}` : ''}${detail}`}
            </Text>
          </Box>
        );
      }
      const label = status === 'partial_failure'
        ? 'TASK PARTIAL FAILURE'
        : status === 'blocked'
          ? 'TASK BLOCKED'
          : 'TASK DONE';
      const suffix = title ? ` ${title}` : '';
      const detail = statusReason ? `: ${statusReason}` : '';
      const color = status === 'blocked' || status === 'partial_failure' ? 'yellow' : 'green';
      return (
        <Box>
          <Text color={color}>{`${label}${suffix}${detail}`}</Text>
        </Box>
      );
    }

    case 'task_deferred': {
      const title = String((event as any).title || '').trim();
      const statusReason = String((event as any).status_reason || '').trim();
      const suffix = title ? ` ${title}` : '';
      const detail = statusReason ? `: ${statusReason}` : '';
      return (
        <Box>
          <Text color="yellow">{`TASK DEFERRED${suffix}${detail}`}</Text>
        </Box>
      );
    }

    case 'thinking':
      return null;

    case 'thinking_end':
      // Don't render anything - this just signals to stop showing thinking indicator
      return null;
      
    case 'delayed_thinking_start':
      // Don't render anything - this is handled by the terminal component
      return null;
      
    case 'termination_reason': {
      // Suppress iteration-limit notifications entirely; SDK governs swarm limits
      const reason = (event as any).reason as string | undefined;
      const msg = (event as any).message as string | undefined;
      if ((typeof reason === 'string' && reason.toLowerCase().includes('swarm')) ||
          (typeof msg === 'string' && msg.toLowerCase().includes('swarm iteration limit'))) {
        return null;
      }
      // Display a simple termination notification (no emojis)
      let reasonLabel = 'TERMINATED';
      switch (reason) {
        case 'complete':
          reasonLabel = 'OPERATION COMPLETE';
          break;
        case 'budget_limit':
          reasonLabel = 'BUDGET LIMIT';
          break;
        case 'user_abort':
          reasonLabel = 'TERMINATED';
          break;
        case 'network_timeout':
        case 'network_error':
        case 'timeout':
          reasonLabel = 'NETWORK TIMEOUT';
          break;
        case 'max_tokens':
          reasonLabel = 'TOKEN LIMIT';
          break;
        case 'rate_limited':
          reasonLabel = 'RATE LIMITED';
          break;
        case 'model_error':
          reasonLabel = 'MODEL ERROR';
          break;
        case 'error':
          reasonLabel = 'TERMINATION ERROR';
          break;
        default:
          reasonLabel = 'TERMINATED';
      }
      const normalizedReason = (reason || '').toLowerCase();
      const sanitizedMessage =
        typeof msg === 'string'
          ? (msg.replace(/\s*Switching to final report\.?/gi, '').trim() || 'Provider/network timeout detected.')
          : msg;
      if (reason === 'complete') {
        return (
          <Box flexDirection="column" marginTop={1} marginBottom={1}>
            <Box borderStyle="round" borderColor="green" paddingX={1}>
              <Text color="green" bold>{reasonLabel}: {sanitizedMessage || 'Assessment workflow completed.'}</Text>
            </Box>
          </Box>
        );
      }
      if (reason === 'error') {
        return (
          <Box flexDirection="column" marginTop={1} marginBottom={1}>
            <Box borderStyle="round" borderColor="red" paddingX={1}>
              <Text color="red" bold>{reasonLabel}: {sanitizedMessage || 'Operation terminated due to an error.'}</Text>
            </Box>
          </Box>
        );
      }
      const likelyNetworkIssue = ['network_timeout', 'network_error', 'timeout'].includes(normalizedReason);
      const providerLabel = (() => {
        const providerId = effectiveConfig?.modelProvider;
        if (!providerId) return 'model provider';
        const labelMap: Record<'bedrock' | 'ollama' | 'litellm' | 'gemini', string> = {
          bedrock: 'AWS Bedrock',
          ollama: 'Ollama',
          litellm: 'LiteLLM',
          gemini: 'Google GenAI'
        };
        return labelMap[providerId] ?? providerId.replace(/_/g, ' ');
      })();
      const helpHints: string[] = [];
      if (likelyNetworkIssue) {
        if (effectiveConfig?.modelProvider) {
          helpHints.push(`Provider configured as "${providerLabel}".`);
        } else {
          helpHints.push('No model provider configured. Open Settings → Provider to configure credentials.');
        }
        if (effectiveConfig?.awsRegion) {
          helpHints.push(`Region set to "${effectiveConfig.awsRegion}". Verify this matches your ${providerLabel} deployment.`);
        }
        if (effectiveConfig?.ollamaHost && effectiveConfig.modelProvider === 'ollama') {
          helpHints.push(`Attempted to reach Ollama at ${effectiveConfig.ollamaHost}. Ensure the host is reachable.`);
        }
        helpHints.push('Confirm credentials/network access, then retry the assessment.');
      }
      return (
        <Box flexDirection="column" marginTop={1} marginBottom={1}>
          <Box borderStyle="round" borderColor="yellow" paddingX={1}>
            <Text color="yellow" bold>{reasonLabel}: {sanitizedMessage}</Text>
          </Box>
          {helpHints.length > 0 && (
            <Box marginLeft={2} marginTop={1} flexDirection="column">
              {helpHints.map((hint, idx) => (
                <Text key={idx} color="yellow">• {hint}</Text>
              ))}
            </Box>
          )}
        </Box>
      );
    }
      
    case 'reasoning':
      // This case should not be reached anymore as reasoning is handled in StreamDisplay
      // But keep it as fallback
      const agentLabel = ('agent_name' in event && (event as any).agent_name)
          ? ` (${(event as any).agent_name})`
        : '';
      return (
        <Box flexDirection="column">
          <Text color="cyan" bold>reasoning{agentLabel}</Text>
          <Box paddingLeft={0}>
            <Text color="cyan">{event.content}</Text>
          </Box>
          <Text> </Text>
        </Box>
      );
      
    case 'tool_start': {
      // Get the latest tool input (may have been updated via tool_input_update)
      // First check if we have updated input in the toolInputs Map, otherwise use the event's tool_input
      const toolId = (event as any).toolId ?? (event as any).tool_id;
      // Prefer the richer input from the current event; fall back to stored map only if event input is absent/empty
      const eventInput = (("tool_input" in event) ? (event as any).tool_input : undefined) as any;
      const hasEventInput = (() => {
        if (eventInput == null) return false;
        if (typeof eventInput === 'string') return eventInput.trim().length > 0;
        if (Array.isArray(eventInput)) return eventInput.length > 0;
        if (typeof eventInput === 'object') return Object.keys(eventInput).length > 0;
        return !!eventInput;
      })();
      const mapInput = (toolId && toolInputs?.get(toolId)) as any;
      const hasMapInput = (() => {
        if (mapInput == null) return false;
        if (typeof mapInput === 'string') return mapInput.trim().length > 0;
        if (Array.isArray(mapInput)) return mapInput.length > 0;
        if (typeof mapInput === 'object') return Object.keys(mapInput).length > 0;
        return !!mapInput;
      })();
      // Choose the input that actually contains the fields needed for rendering.
      // In particular, python_repl often arrives with a trimmed eventInput (code omitted by normalizer),
      // while a subsequent tool_input_update holds the full code. Prefer the map input in that case.
      const getPyCode = (inp: any): string => {
        if (!inp) return '';
        const v = inp.code ?? inp.source ?? inp.input ?? inp.code_preview;
        return typeof v === 'string' ? v : (v != null ? (() => { try { return JSON.stringify(v); } catch { return String(v); } })() : '');
      };
      let latestInput: any = eventInput;
      if (event.tool_name === 'python_repl') {
        const evCode = getPyCode(eventInput);
        const mapCode = getPyCode(mapInput);
        if ((!evCode || evCode.trim().length === 0) && (mapCode && mapCode.trim().length > 0)) {
          latestInput = mapInput;
        } else if (!hasEventInput && hasMapInput) {
          latestInput = mapInput;
        }
      } else {
        latestInput = hasEventInput ? eventInput : (hasMapInput ? mapInput : {});
      }
      const agentContext = ('agent_name' in event && (event as any).agent_name)
        ? ` (${(event as any).agent_name})`
        : '';
      
      // Always show a tool header even if args are not yet available.
      // Individual tool renderers will gracefully handle missing fields.
      // Otherwise, handle specific tool formatting
      switch (event.tool_name) {
        case 'swarm':
          // Simplified swarm tool header to avoid duplication
          // Ensure agents is an array before processing
          const agents = Array.isArray(latestInput?.agents) ? latestInput.agents : [];
          const agentCount = agents.length || 0;
          const agentNames = agents.map((a: any) =>
            typeof a === 'string' ? a : (a?.name || 'agent')
          ).filter(Boolean).slice(0, 4).join(', ') || 'agents';

          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="yellow" bold>tool: swarm{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>└─ deploying {agentCount} agents: {agentNames}</Text>
              </Box>
            </Box>
          );
        case 'memory_get':
        case 'memory_retrieve':
        case 'memory_list': {
          const action = event.tool_name.substring(7);
          const rawContent = latestInput?.plan || latestInput?.content || latestInput?.query || '';
          // Ensure content is always a string (handle plan objects, etc.)
          let content: string;
          if (typeof rawContent === 'string') {
            content = rawContent;
          } else if (rawContent && typeof rawContent === 'object') {
            try {
              content = JSON.stringify(rawContent);
            } catch {
              content = String(rawContent);
            }
          } else {
            content = String(rawContent);
          }
          const preview = (content.length > 60 ? content.substring(0, 60) + '...' : content);
          const labelKey =
              action === 'store'
                ? 'content'
                : 'query';

          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: {event.tool_name}{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>├─ action: {action === 'store' ? 'storing' : action === 'retrieve' ? 'retrieving' : action}</Text>
              </Box>
              {preview && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ {labelKey}: {preview}</Text>
                </Box>
              )}
              {!preview && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ </Text>
                </Box>
              )}
            </Box>
          );
        }

        case 'store_observation':
        case 'store_knowledge':
        case 'store_finding':
        case 'record_finding_validation': {
          const preview = formatToolInput(event.tool_name, latestInput);
          const color = event.tool_name === 'store_finding' || event.tool_name === 'record_finding_validation'
            ? 'yellow'
            : 'green';
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color={color} bold>tool: {event.tool_name}{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>└─ {preview}</Text>
              </Box>
            </Box>
          );
        }
          
        case 'shell': {
          // Show tool header with command(s) if available

          // Pull raw commands from the most permissive set of fields
          const rawInput: any = (latestInput as any) || {};
          let raw = rawInput.commands ?? rawInput.command ?? rawInput.cmd ?? rawInput.input ?? '';

          // Helper to stringify any command entry into a single shell line
          const stringifyCommandEntry = (entry: any): string => {
            if (entry === null || entry === undefined) return '';
            if (typeof entry === 'string') return entry;
            if (Array.isArray(entry)) {
              // Join parts (args arrays)
              const parts = entry.map((p) => stringifyCommandEntry(p)).filter(Boolean);
              return parts.join(' ');
            }
            if (typeof entry === 'object') {
              // Prefer well-known keys in order
              if ('command' in entry) return stringifyCommandEntry((entry as any).command);
              if ('cmd' in entry) return stringifyCommandEntry((entry as any).cmd);
              if ('value' in entry) return stringifyCommandEntry((entry as any).value);
              if ('args' in entry) return stringifyCommandEntry((entry as any).args);
              // Last resort - structured but unknown: JSON.stringify to avoid [object Object]
              try {
                return JSON.stringify(entry);
              } catch {
                return String(entry);
              }
            }
            return String(entry);
          };

          // Normalize raw into a string[] of commands
          let commands: any[] = [];
          try {
            if (Array.isArray(raw)) {
              commands = raw.map((e: any) => stringifyCommandEntry(e)).filter(Boolean);
            } else if (typeof raw === 'string') {
              const trimmed = raw.trim();
              if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
                // JSON-looking string: try to parse
                try {
                  const parsed = JSON.parse(trimmed);
                  if (Array.isArray(parsed)) {
                    commands = parsed.map((e: any) => stringifyCommandEntry(e)).filter(Boolean);
                  } else {
                    const s = stringifyCommandEntry(parsed);
                    if (s) commands = [s];
                  }
                } catch {
                  // Fallback: keep as single command line
                  if (trimmed) commands = [trimmed];
                }
              } else {
                if (trimmed) commands = [trimmed];
              }
            } else if (typeof raw === 'object' && raw) {
              const s = stringifyCommandEntry(raw);
              if (s) commands = [s];
            }
          } catch {
            // If anything fails, ensure commands is at least empty array
            commands = Array.isArray(raw) ? raw.map((e: any) => String(e)).filter(Boolean) : [];
          }

          // Final safety: ensure all commands are strings to avoid "[object Object]"
          const toDisplayString = (x: any): string => {
            if (typeof x === 'string') return x;
            try { return JSON.stringify(x); } catch { return String(x); }
          };
          const displayCommands: string[] = commands.map(toDisplayString).filter(Boolean);

          // Display commands with timeout/parallel info if available
          const hasTimeout = rawInput?.timeout;
          const hasParallel = rawInput?.parallel;
          const extraParams = [] as string[];
          if (hasTimeout) extraParams.push(`timeout: ${rawInput.timeout}s`);
          if (hasParallel) extraParams.push('parallel execution');

          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: shell{agentContext}</Text>
              {displayCommands.length > 0 ? (
                displayCommands.map((cmd, index) => {
                  const isLastCommand = index === displayCommands.length - 1 && extraParams.length === 0;
                  const prefix = isLastCommand ? '└─' : '├─';
                  return (
                    <Box key={index} marginLeft={2}>
                      <Text dimColor>{prefix} {cmd}</Text>
                    </Box>
                  );
                })
              ) : (
                <Box marginLeft={2}>
<Text dimColor>└─ (awaiting args …)</Text>
                </Box>
              )}
              {extraParams.length > 0 && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ {extraParams.join(' | ')}</Text>
                </Box>
              )}
            </Box>
          );
        }
          
        case 'http_request': {
const method = latestInput.method || 'GET';
          const url = latestInput.url || '';
          const urlDisplay = url && url.trim().length > 0 ? url : '(awaiting args …)';
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: http_request{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>├─ method: {method}</Text>
              </Box>
              <Box marginLeft={2}>
<Text dimColor>└─ url: {urlDisplay}</Text>
              </Box>
            </Box>
          );
        }

        case 'browser_goto_url': {
          const url = latestInput.url || '';
          const urlDisplay = url && url.trim().length > 0 ? url : '(awaiting args …)';
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: browser_goto_url{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>└─ url: {urlDisplay}</Text>
              </Box>
            </Box>
          );
        }

        case 'browser_perform_action': {
          const action = latestInput.action || '';
          const actionDisplay = action && action.trim().length > 0 ? action : '(awaiting args …)';
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: browser_perform_action{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>└─ action: {actionDisplay}</Text>
              </Box>
            </Box>
          );
        }

        case 'browser_observe_page': {
          const instruction = latestInput.instruction || '';
          const instructionDisplay = instruction && instruction.trim().length > 0 ? instruction : '(awaiting args …)';
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: browser_observe_page{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>└─ instruction: {instructionDisplay}</Text>
              </Box>
            </Box>
          );
        }

        case 'browser_evaluate_js': {
          const expression = latestInput.expression || '';
          const expressionDisplay = expression && expression.trim().length > 0 ? expression : '(awaiting args …)';
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: browser_evaluate_js{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>└─ instruction: {expressionDisplay}</Text>
              </Box>
            </Box>
          );
        }

        case 'browser_get_cookies': {
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: browser_get_cookies{agentContext}</Text>
            </Box>
          );
        }

        case 'browser_get_page_html': {
          // This tool operates on the browser's current page and has no input arguments.
          // Still, show a short description so the user understands what is happening.
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: browser_get_page_html{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>└─ capture HTML of the current page and save as an artifact</Text>
              </Box>
            </Box>
          );
        }

        case 'browser_set_headers': {
          const headers = latestInput.headers || {};
          const headersDisplay = Object.keys(headers).length > 0 ? JSON.stringify(headers) : '(awaiting args …)';
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: browser_set_headers{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>├─ headers: {headersDisplay}</Text>
              </Box>
            </Box>
          );
        }

        case 'file_write': {
          const filePath = latestInput.path || 'unknown';
          const fileContent = latestInput.content || '';
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: file_write{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>├─ path: {filePath}</Text>
              </Box>
              {fileContent && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ size: {fileContent.length} chars</Text>
                </Box>
              )}
            </Box>
          );
        }
          
        case 'editor': {
          const editorCmd = latestInput.command || 'edit';
          const editorPath = latestInput.path || '';
          // Support multiple possible input fields for content
          const editorContent = latestInput.content ?? latestInput.file_text ?? latestInput.text ?? '';
          // Compute size in lines if we have text content
          const contentStr = typeof editorContent === 'string' ? editorContent : (() => { try { return JSON.stringify(editorContent, null, 2); } catch { return String(editorContent ?? ''); } })();
          const lineCount = contentStr ? (contentStr.split('\n').length) : 0;
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: editor{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>├─ command: {editorCmd}</Text>
              </Box>
              <Box marginLeft={2}>
                <Text dimColor>{(contentStr && contentStr.length > 0) ? '├─' : '└─'} path: {editorPath}</Text>
              </Box>
              {(contentStr && contentStr.length > 0) && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ size: {lineCount} {lineCount === 1 ? 'line' : 'lines'}</Text>
                </Box>
              )}
            </Box>
          );
        }
          
        
        case 'think': {
          // think output goes to reasoning, but still show tool invocation
          const thought = latestInput.thought || latestInput.content || '';
          
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: think{agentContext}</Text>
              {thought && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ {thought.length > 100 ? thought.substring(0, 100) + '...' : thought}</Text>
                </Box>
              )}
            </Box>
          );
        }
          
        case 'python_repl': {
          const code = (latestInput && (latestInput.code ?? latestInput.source ?? latestInput.input ?? latestInput.code_preview)) || '';
          const codeStr = typeof code === 'string' ? code : (() => { try { return JSON.stringify(code, null, 2); } catch { return String(code ?? ''); } })();
          const codeLines = codeStr.split('\n');
          const previewLines = 8; // Increased from 5 to show more context
          let codeDisplayLines: string[];
          if (!codeStr || codeStr.trim().length === 0) {
            codeDisplayLines = [];
          } else if (codeLines.length <= previewLines) {
            codeDisplayLines = codeLines;
          } else {
            // Show first 6 lines and last 2 lines for better context
            codeDisplayLines = [
              ...codeLines.slice(0, 6),
              '',
              `... (${codeLines.length - 8} more lines)`,
              '',
              ...codeLines.slice(-2)
            ];
          }
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: python_repl{agentContext}</Text>
              <Box marginLeft={2} flexDirection="column">
                <Text dimColor>└─ code:</Text>
                {codeDisplayLines.length === 0 ? (
                  <Box marginLeft={5}><Text dimColor>(waiting for code input)</Text></Box>
                ) : (
                  <Box marginLeft={5} flexDirection="column">
                    {codeDisplayLines.map((line, index) => {
                      // Don't show tree characters for code content
                      if (line.startsWith('...')) {
                        return <Text key={index} dimColor italic>    {line}</Text>;
                      }
                      return <Text key={index} dimColor>    {line || ' '}</Text>;
                    })}
                  </Box>
                )}
              </Box>
            </Box>
          );
        }
          
        case 'report_generator': {
          const target = latestInput.target || 'unknown';
          const reportType = latestInput.report_type || latestInput.type || 'general';
          return (
            <Box flexDirection="column">
              <Text color="green" bold>tool: report_generator{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>├─ target: {target}</Text>
              </Box>
              <Box marginLeft={2}>
                <Text dimColor>└─ type: {reportType}</Text>
              </Box>
            </Box>
          );
        }
          
        case 'handoff_to_agent': {
          // Prefer explicit agent_name (set by backend), then handoff_to, then other fallbacks
          const handoffTo = latestInput.agent_name || latestInput.handoff_to || latestInput.agent || latestInput.target_agent || 'unknown';
          const handoffMsg = latestInput.message || '';
          const msgPreview = handoffMsg.length > 80 ? handoffMsg.substring(0, 80) + '...' : handoffMsg;
          return (
            <Box flexDirection="column">
              <Text color="green" bold>tool: handoff_to_agent{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>├─ handoff_to: {handoffTo}</Text>
              </Box>
              {msgPreview && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ message: {msgPreview}</Text>
                </Box>
              )}
            </Box>
          );
        }
          
        case 'load_tool': {
          const toolName = latestInput.tool_name || latestInput.tool || latestInput.name || 'unknown';
          const toolPath = latestInput.path || '';
          const toolDescription = latestInput.description || '';
          const hasPath = !!toolPath;
          const hasDesc = !!toolDescription;
          return (
            <Box flexDirection="column">
              <Text color="green" bold>tool: load_tool{agentContext}</Text>
              <Box marginLeft={2}>
                <Text dimColor>{hasPath || hasDesc ? '├─' : '└─'} loading: {toolName}</Text>
              </Box>
              {toolPath && (
                <Box marginLeft={2}>
                  <Text dimColor>{hasDesc ? '├─' : '└─'} path: {toolPath}</Text>
                </Box>
              )}
              {toolDescription && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ description: {toolDescription}</Text>
                </Box>
              )}
            </Box>
          );
        }
          
        default: {
          // Enhanced tool display with agent context and structured parameters
          
          // Check if tool_input is an object with multiple properties for structured display
          const toolInput = latestInput;
          const isStructuredInput = toolInput && typeof toolInput === 'object' && !Array.isArray(toolInput);
          
          if (isStructuredInput && Object.keys(toolInput).length > 0) {
            // Display structured parameters with tree format
            const params = Object.entries(toolInput);
            const paramCount = params.length;
            
            return (
              <Box flexDirection="column" marginTop={1}>
                <Text color="green" bold>tool: {event.tool_name}{agentContext}</Text>
                {params.map(([key, value], index) => {
                  const isLast = index === paramCount - 1;
                  const prefix = isLast ? '└─' : '├─';
                  
                  // Format value based on type
                  let displayValue: string;
                  if (value === null || value === undefined) {
                    displayValue = 'null';
                  } else if (typeof value === 'string') {
                    // Truncate long strings
                    displayValue = value.length > 100 ? value.substring(0, 100) + '...' : value;
                  } else if (Array.isArray(value)) {
                    displayValue = `[${value.length} items]`;
                  } else if (typeof value === 'object') {
                    displayValue = '{...}';
                  } else {
                    displayValue = String(value);
                  }
                  
                  return (
                    <Box key={key} marginLeft={2}>
                      <Text dimColor>{prefix} {key}: {displayValue}</Text>
                    </Box>
                  );
                })}
              </Box>
            );
          }
          
          // Fallback to single-line preview for non-structured inputs
          const preview = formatToolInput(event.tool_name as any, toolInput);
          
          return (
            <Box flexDirection="column" marginTop={1}>
              <Text color="green" bold>tool: {event.tool_name}{agentContext}</Text>
              {preview && (
                <Box marginLeft={2}>
                  <Text dimColor>└─ {preview}</Text>
                </Box>
              )}
            </Box>
          );
        }
      }
    }

    case 'command':
          // Robustly derive a displayable command string from event.content
          let commandText: string = '';
          try {
            if (typeof event.content === 'string') {
              const raw = event.content.trim();
              if (raw.startsWith('{')) {
                try {
                  const parsed = JSON.parse(raw);
                  commandText = parsed && parsed.command ? String(parsed.command) : raw;
                } catch {
                  commandText = raw; // Keep as-is if JSON parse fails
                }
              } else {
                commandText = raw;
              }
            } else if (event.content && typeof event.content === 'object') {
              // Avoid [object Object] by JSON stringifying unknown structures
              try {
                commandText = JSON.stringify(event.content);
              } catch {
                commandText = String(event.content);
              }
            } else {
              commandText = String(event.content ?? '');
            }
          } catch {
            commandText = String((event as any).content ?? '');
          }
          
          return (
            <Box flexDirection="column" marginLeft={2}>
              <Text><Text dimColor>├─</Text> {commandText}</Text>
            </Box>
          );

    case 'report_content': {
      // Keep the event for the final report fallback viewer, but do not emit a
      // second inline body snippet into the streaming TUI.
      return null;
    }

    case 'report_paths': {
      const opId = (event as any).operation_id || '';
      const target = (event as any).target || '';
      const outputBaseDir = (() => {
        const configured = effectiveConfig.outputDir || './outputs';
        return path.isAbsolute(configured)
          ? configured
          : path.resolve(projectRoot || process.cwd(), configured);
      })();
      const displayPath = (raw: string): string => mapContainerReportPath(raw, outputBaseDir);
      const outputDir = displayPath((event as any).outputDir ?? (event as any).output_dir ?? '');
      const reportPath = displayPath((event as any).reportPath ?? (event as any).report_path ?? '');
      const logPath = displayPath((event as any).logPath ?? (event as any).log_path ?? '');
      const artifactsPath = displayPath((event as any).artifactsPath ?? (event as any).artifacts_path ?? '');
      const fields = [opId, target, outputDir, reportPath, logPath, artifactsPath];
      return (
        <Box flexDirection="column" marginTop={1} marginBottom={1}>
          <Box borderStyle="round" borderColor="green" paddingX={1}>
            <Text color="green" bold>ARTIFACTS AND LOGS</Text>
          </Box>
          <Box flexDirection="column" marginTop={1} paddingX={1}>
            {opId ? (<Text>Operation ID: {opId}</Text>) : null}
            {target ? (<Text>Target: {target}</Text>) : null}
            {outputDir ? (<Text>Operation Path: {outputDir}</Text>) : null}
            {reportPath ? (<Text>Report: {reportPath}</Text>) : null}
            {logPath ? (<Text>Log: {logPath}</Text>) : null}
            {artifactsPath ? (<Text>Artifacts: {artifactsPath}</Text>) : null}
            {fields.every(value => !value) ? (<Text dimColor>Paths unavailable</Text>) : null}
          </Box>
        </Box>
      );
    }

    case 'operation_terminated': {
      const coverage = Array.isArray((event as any).workflow_coverage_summary)
        ? (event as any).workflow_coverage_summary
        : [];
      const complete = Boolean((event as any).completion_status?.assessment_complete);
      return (
        <Box flexDirection="column" marginTop={1} paddingX={1}>
          <Text color={complete ? 'green' : 'yellow'} bold>
            {complete ? 'OPERATION TERMINATED: ASSESSMENT COMPLETE' : 'OPERATION TERMINATED: INCOMPLETE'}
          </Text>
          <Text>Reason: {(event as any).termination_reason || 'unknown'}</Text>
          <Text>Coverage: {coverage.length} phase(s)</Text>
        </Box>
      );
    }

    case 'operation_finalized': {
      return (
        <Box flexDirection="column" marginTop={1} paddingX={1}>
          <Text color="green" bold>OPERATION FINALIZED</Text>
          <Text>Report: {(event as any).report_status || 'unknown'} | Evaluation: {(event as any).evaluation_status || 'not_run'}</Text>
        </Box>
      );
    }

    case 'output': {
      // Render even when content is empty to preserve intentional spacing.
      if ((event as any).content == null) {
        return null;
      }

      let contentStr: string;
      if (typeof (event as any).content === 'string') {
        contentStr = (event as any).content as string;
      } else {
        try {
          contentStr = JSON.stringify((event as any).content, null, 2);
        } catch {
          contentStr = String((event as any).content);
        }
      }

      // Normalize line endings and fix occasionally inlined tokens
      const normalized = contentStr
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')
        // If "Command:" was concatenated onto a previous line without a newline, insert one.
        .replace(/(\S)Command:/g, '$1\nCommand:');

      // Strip ANSI escape codes from tool output to prevent terminal formatting issues
      const plain = stripAnsi(normalized);

      // Skip only placeholder tokens if the entire content is just a token
      const plainTrimmed = plain.trim();
      if (/^(output|reasoning)(\s*\[[^\]]+\])?$/i.test(plainTrimmed)) {
        return null;
      }
      
      // Intelligent detection: If content looks like structured data (JSON array/object),
      // it's likely tool output that should be displayed even without metadata
      const looksLikeToolOutput = plainTrimmed.startsWith('[') || plainTrimmed.startsWith('{');
      
      // Check if this output is from a tool buffer (either explicit metadata or inferred)
      const eventMetadata = (event as any).metadata || {};
      const fromToolBuffer = eventMetadata.fromToolBuffer || looksLikeToolOutput;

      // Suppress React application operational logs (timestamps + app status lines)
      const appLogPatterns: RegExp[] = [
        /^\s*\[[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s[AP]M\]\sStarting\s.+\sassessment\son\s/i,
        /^\s*\[[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s[AP]M\]\sOperation ID:/i,
        /^\s*\[[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s[AP]M\]\sExecution Mode:/i,
        /^\s*\[[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s[AP]M\]\sSelecting execution service/i,
        /^\s*\[[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s[AP]M\]\sSelected execution mode:/i,
        /^\s*\[[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s[AP]M\]\sLaunching\s.+\sassessment\sexecution/i,
      ];
      // We'll apply per-line filtering below to catch bundled events too

      // Apply per-line filtering to handle events containing multiple lines
      // But preserve JSON content for tool outputs
      const filteredLinesPre = plain.split('\n').filter(line => {
        const l = line.trim();
        if (l.length === 0) return true; // keep blank spacers
        // Suppress duplicate stop-cycle noise (reason is shown via metadata/termination panel)
        if (l.startsWith('Event loop cycle stop requested')) return false;
        // Remove python_repl success banner lines
        if (/^Code executed successfully\.?$/i.test(l)) return false;
        // Drop placeholder lines, including forms like "output [11 lines]"
        if (/^(output|reasoning)(\s*\[[^\]]+\])?$/i.test(l)) return false;
        // Drop empty Error: labels
        if (/^Error:\s*$/.test(l)) return false;
        // For tool outputs (JSON), keep all content
        if (fromToolBuffer) {
          // Only drop CYBER_EVENT and timestamp logs for tool outputs
          if (l.startsWith('__CYBER_EVENT__') || l.endsWith('__CYBER_EVENT_END__')) return false;
          if (/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+-\s+(INFO|DEBUG|WARNING|ERROR)\s+-\s+/.test(l)) return false;
          // Suppress noisy parser errors that could appear during large report emission
          if (/^Error parsing event:/i.test(l)) return false;
          return true;
        }
        // For non-tool outputs, apply normal filtering
        // Drop raw CYBER_EVENT payload lines
        if (l.startsWith('__CYBER_EVENT__') || l.endsWith('__CYBER_EVENT_END__')) return false;
        // Drop ISO timestamped app logs: 2025-08-16 16:59:17 - INFO - ...
        if (/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+-\s+(INFO|DEBUG|WARNING|ERROR)\s+-\s+/.test(l)) return false;
        // Drop [3:19:46 PM]-style app logs
        if (appLogPatterns.some(p => p.test(l))) return false;
        // Suppress noisy parser errors globally as well
        if (/^Error parsing event:/i.test(l)) return false;
        return true;
      });
      if (filteredLinesPre.length === 0) {
        return null;
      }
      const filtered = filteredLinesPre.join('\n');

      // Do not suppress lifecycle ticks anymore; show them in the stream for user visibility.

      // Extract metadata if present in the event (e.g., source, duration)
      const metadata: string[] = [];
      if ('timestamp' in (event as any) && (event as any).timestamp) {
        // Optionally display timestamps in debug mode (kept minimal here)
      }

      // Startup/system messages: format lifecycle/status lines using original symbols (✓/○)
      // IMPORTANT: Do NOT apply this styling to tool outputs
      if (!fromToolBuffer && filtered && (
        filtered.startsWith('▶') ||
        filtered.startsWith('◆') ||
        filtered.trim().startsWith('✓') ||
        filtered.trim().startsWith('✓') ||
        filtered.trim().startsWith('○') ||
        filtered.startsWith('[Observability]')
      )) {
        if (filtered.startsWith('▶')) {
          // Initializing messages
          return (
            <Text color="#89B4FA" bold>{filtered}</Text>
          );
        } else if (filtered.startsWith('◆')) {
          // System status messages
          const isComplete = filtered.toLowerCase().includes('ready') || filtered.toLowerCase().includes('complete');
          return (
            <Text color={isComplete ? '#A6E3A1' : '#89DCEB'}>{filtered}</Text>
          );
        } else if (filtered.trim().startsWith('✓')) {
          // Success indicators
          return (
            <Box marginLeft={1}>
              <Text color="#A6E3A1">{filtered}</Text>
            </Box>
          );
        } else if (filtered.trim().startsWith('○')) {
          // Warning/unavailable indicators
          return (
            <Box marginLeft={1}>
              <Text color="#F9E2AF">{filtered}</Text>
            </Box>
          );
        } else if (filtered.startsWith('[Observability]')) {
          return (
            <Text color="#CBA6F7">{filtered}</Text>
          );
        }
      }
      
      // For command output, show with consistent spacing
      const lines = filtered.split('\n');
      // Collapse consecutive duplicate lines within the same event to avoid visual spam
      const dedupedLines: string[] = [];
      for (const line of lines) {
        if (dedupedLines.length === 0 || dedupedLines[dedupedLines.length - 1] !== line) {
          dedupedLines.push(line);
        }
      }
      
      // Check if this is a security report or important system output
      const isReport = contentStr.includes('# SECURITY ASSESSMENT REPORT') || 
                      contentStr.includes('# CTF Challenge Assessment Report') ||
                      contentStr.includes('EXECUTIVE SUMMARY') ||
                      contentStr.includes('KEY FINDINGS') ||
                      contentStr.includes('REMEDIATION ROADMAP');
      
      // Simple and elegant: Only show operation summary for actual completion messages
      // These messages come from the main agent flow, not from tools
      const isOperationSummary = !fromToolBuffer && (
                                 contentStr.includes('Outputs stored in:') ||
                                 contentStr.includes('Memory stored in:') ||
                                 contentStr.includes('Report saved to:') ||
                                 contentStr.includes('Operation ID:') ||
                                 contentStr.includes('REPORT ALSO SAVED TO:') ||
                                 contentStr.includes('OPERATION LOGS:'));
      
      const collapseThreshold = isReport ? DISPLAY_LIMITS.REPORT_MAX_LINES : 
                               (isOperationSummary ? DISPLAY_LIMITS.OPERATION_SUMMARY_LINES : 
                                (fromToolBuffer ? DISPLAY_LIMITS.TOOL_OUTPUT_COLLAPSE_LINES : 
                                 DISPLAY_LIMITS.DEFAULT_COLLAPSE_LINES));
      let shouldCollapse = dedupedLines.length > collapseThreshold;
      
      // Fallback: if content is essentially one giant line (minified or escaped \n) and very large, apply char-based collapse
      if (!shouldCollapse) {
        const totalLen = contentStr.length;
        const newlineCount = (contentStr.match(/\n/g) || []).length;
        const needsCharCollapse = totalLen > (DISPLAY_LIMITS.OUTPUT_PREVIEW_CHARS + DISPLAY_LIMITS.OUTPUT_TAIL_CHARS + 200) && newlineCount < 5;
        if (needsCharCollapse) {
          shouldCollapse = true;
        }
      }
      
      let displayLines: string[];
      if (shouldCollapse && fromToolBuffer) {
        // For tool outputs, show generous head/tail with a continuation marker
        if (dedupedLines.length > DISPLAY_LIMITS.TOOL_OUTPUT_COLLAPSE_LINES) {
          displayLines = [
            ...dedupedLines.slice(0, DISPLAY_LIMITS.TOOL_OUTPUT_PREVIEW_LINES),
            '... (content continues)',
            ...dedupedLines.slice(-DISPLAY_LIMITS.TOOL_OUTPUT_TAIL_LINES)
          ];
        } else {
          // Char-based fallback when lines are not informative
          const s = contentStr;
          const head = s.slice(0, DISPLAY_LIMITS.OUTPUT_PREVIEW_CHARS);
          const tail = s.slice(-DISPLAY_LIMITS.OUTPUT_TAIL_CHARS);
          displayLines = [head, '... (content continues)', tail];
        }
      } else if (shouldCollapse && !isReport && !isOperationSummary) {
        // For normal output, prefer line-based collapse when there are lines; otherwise, use char-based collapse
        if (dedupedLines.length > DISPLAY_LIMITS.DEFAULT_COLLAPSE_LINES) {
          displayLines = [...dedupedLines.slice(0, 5), '... (content continues)', ...dedupedLines.slice(-3)];
        } else {
          const s = contentStr;
          const head = s.slice(0, Math.min(DISPLAY_LIMITS.OUTPUT_PREVIEW_CHARS, Math.floor(s.length * 0.8)));
          const tail = s.slice(-DISPLAY_LIMITS.OUTPUT_TAIL_CHARS);
          displayLines = [head, '... (content continues)', tail];
        }
      } else if (shouldCollapse && (isReport || isOperationSummary)) {
        // For reports and summaries, show much more content
        if (isReport) {
          // For reports, show configured preview and tail lines
          displayLines = [
            ...dedupedLines.slice(0, DISPLAY_LIMITS.REPORT_PREVIEW_LINES), 
            '', 
            '... (content continues)', 
            '', 
            ...dedupedLines.slice(-DISPLAY_LIMITS.REPORT_TAIL_LINES)
          ];
        } else {
          // For operation summaries, show all content up to limit
          displayLines = dedupedLines.slice(0, DISPLAY_LIMITS.OPERATION_SUMMARY_LINES);
        }
      } else {
        // Show all lines if under threshold or expanded
        displayLines = dedupedLines;
      }
      
      // Enhanced styling for final reports and operation summaries
      if (isReport) {
        return (
          <Box flexDirection="column" marginTop={1}>
            <Box>
              <Text color="green" bold>📋 FINAL REPORT</Text>
              {metadata.length > 0 && <Text dimColor> ({metadata.join(', ')})</Text>}
            </Box>
            <Box marginLeft={2} flexDirection="column">
              {displayLines.map((line: string, index: number) => (
                <Text key={index}>{line}</Text>
              ))}
            </Box>
            <Text> </Text>
          </Box>
        );
      }
      
      if (isOperationSummary) {
        return (
          <Box flexDirection="column" marginTop={1}>
            <Box>
              <Text color="green" bold>📁 OPERATION COMPLETE</Text>
              {metadata.length > 0 && <Text dimColor> ({metadata.join(', ')})</Text>}
            </Box>
            <Box marginLeft={2} flexDirection="column">
              {displayLines.map((line: string, index: number) => {
                // Highlight path lines
                if (line.includes('Outputs stored in:') || line.includes('Memory stored in:') || 
                    line.includes('Host:') || line.includes('Container:')) {
                  return <Text key={index} color="cyan" bold>{line}</Text>;
                }
                return <Text key={index}>{line}</Text>;
              })}
            </Box>
            <Text> </Text>
          </Box>
        );
      }

      // Show tool output with special formatting
      if (fromToolBuffer) {
        const toolNameMeta = (event as any).metadata?.tool as string | undefined;
        const isPy = toolNameMeta === 'python_repl';
        const headerText = isPy ? 'output (python_repl)' : 'output';
        const showCount = isPy ? dedupedLines.length > 0 : dedupedLines.length > 10;
        return (
          <Box flexDirection="column" marginTop={1}>
            <Box>
              <Text color="yellow">{headerText}</Text>
              {showCount && <Text dimColor> [{dedupedLines.length} lines]</Text>}
              {metadata.length > 0 && <Text dimColor> ({metadata.join(', ')})</Text>}
            </Box>
            <Box marginLeft={2} flexDirection="column">
              {isPy && dedupedLines.length === 0 ? (
                <Text dimColor>(no stdout)</Text>
              ) : (
                displayLines.map((line: string, index: number) => (
                  <Text key={index} dimColor>{line}</Text>
                ))
              )}
            </Box>
            <Text> </Text>
          </Box>
        );
      }
      
      // Regular output (not tool output)
      return (
        <Box flexDirection="column" marginTop={1}>
          <Box>
            <Text color="yellow">output</Text>
            {metadata.length > 0 && <Text dimColor> ({metadata.join(', ')})</Text>}
            {shouldCollapse && <Text dimColor> [{dedupedLines.length} lines, truncated]</Text>}
          </Box>
          <Box marginLeft={2} flexDirection="column">
            {displayLines.map((line, index) => (
              <Text key={index} dimColor>{line}</Text>
            ))}
          </Box>
          <Text> </Text>
        </Box>
      );
    }
    
    case 'tool_output': {
      // Standardized tool output from backend protocol
      if (!('output' in event)) {
        return null;
      }
      
      const toolName = String((event as any).tool || (event as any).tool_name || (event as any).toolName || 'tool');
      const toolStatus = (event.status as string) || 'success';
      const output = event.output as any;
      
      // Extract text content
      const outputText = (() => {
        if (typeof output === 'string') return output;
        if (output == null) return '';
        if (typeof output.text === 'string') return output.text;
        const stdout = typeof output.stdout === 'string' ? output.stdout : '';
        const stderr = typeof output.stderr === 'string' ? output.stderr : '';
        if (stdout || stderr) return [stdout, stderr].filter(Boolean).join('\n');
        if (Array.isArray(output)) return output.map(item => String(item)).join('\n');
        try {
          return JSON.stringify(output, null, 2);
        } catch {
          return String(output);
        }
      })();
      
      if (!outputText.trim()) {
        return null;
      }
      
      return (
        <Box flexDirection="column" marginTop={1}>
          <Box>
            <Text color={toolStatus === 'error' ? 'red' : 'green'}>
              {toolName}: {toolStatus}
            </Text>
          </Box>
          <Box marginLeft={2}>
            <Text>{outputText}</Text>
          </Box>
        </Box>
      );
    }
      
      
    case 'error': {
      // Enhanced error display with solution guidance
      const errorMsg = (event as any).error || event.content || 'Unknown error';
      const solution = (event as any).solution;
      const exitCode = (event as any).exitCode;

      return (
        <Box flexDirection="column" marginTop={1} borderStyle="round" borderColor="red" paddingX={1}>
          <Text bold color="red">✗ Error</Text>
          <Text color="red">{errorMsg}</Text>
          {exitCode !== undefined && (
            <Text dimColor color="red">Exit code: {exitCode}</Text>
          )}
          {solution && (
            <Box flexDirection="column" marginTop={1}>
              <Text bold color="yellow">→ Solution:</Text>
              <Text color="yellow">{solution}</Text>
            </Box>
          )}
        </Box>
      );
    }
      
    case 'metadata': {
      // Render metadata events normally.
      if (!event.content || typeof event.content !== 'object') return null;
      const entries = Object.entries(event.content);
      if (entries.length === 0) return null;

      // Suppress duplicate completion-notification metadata; completion reason is shown separately.
      if (entries.length === 1 && entries[0][0] === 'stopping') {
        return null;
      }

      return (
        <Box flexDirection="column" marginLeft={2}>
          {entries.map(([key, value], index) => {
            const isLast = index === entries.length - 1;
            let displayValue: string;
            if (value === null || value === undefined) {
              displayValue = 'null';
            } else if (typeof value === 'object') {
              try {
                const json = JSON.stringify(value);
                displayValue = json.length > 50 ? json.substring(0, 50) + '...' : json;
              } catch {
                displayValue = '[object]';
              }
            } else {
              const s = String(value);
              displayValue = s.length > 50 ? s.substring(0, 50) + '...' : s;
            }
            return (
              <Box key={index}>
                <Text dimColor>{isLast ? '└─' : '├─'} {key}: {displayValue}</Text>
              </Box>
            );
          })}
        </Box>
      );
    }
      
    case 'divider':
      return null;

    case 'separator':
      return null;

    case 'user_handoff':
      return (
        <>
          <Text color="green" bold>AGENT REQUESTING USER INPUT</Text>
          <Box borderStyle="round" borderColor="green" paddingX={1} marginY={1}>
            <Text>{event.message}</Text>
          </Box>
          {event.breakout ? (
            <Text color="red" bold>Agent execution will stop after this handoff</Text>
          ) : null}
          <Box marginTop={1}>
            <Text color="yellow" bold>➤ Type your response below and press Enter to send it to the agent</Text>
          </Box>
          <Text> </Text>
        </>
      );
      
    case 'batch':
      // Handle batched events from backend
      // Recursively render each event in the batch
      if (!('events' in event) || !Array.isArray(event.events)) {
        return null;
      }
      return (
        <>
          {event.events.map((batchedEvent, idx) => (
            <MemoizedEventLine 
              key={batchedEvent.id || `${event.id}_${idx}`}
              event={batchedEvent}
              toolInputs={toolInputs}
              animationsEnabled={animationsEnabled}
              operationContext={operationContext}
              reportPath={reportPath}
              reportFallbackContent={reportFallbackContent}
              projectRoot={projectRoot}
            />
          ))}
        </>
      );
      
    case 'operation_init':
      // Display comprehensive operation initialization info (preflight checks)
      if (!event || typeof event !== 'object') return null;
      
      return (
        <Box flexDirection="column">
          <Text color="#89B4FA" bold>◆ Operation initialization complete</Text>
          
          {/* Operation Details */}
          {('operation_id' in event && event.operation_id) ? (
            <Text dimColor>  Operation ID: {event.operation_id}</Text>
          ) : null}
          {('target' in event && event.target) ? (
            <Text dimColor>  Target: {event.target}</Text>
          ) : null}
          {('objective' in event && event.objective) ? (
            <Text dimColor>  Objective: {event.objective}</Text>
          ) : null}
          {('model_id' in event && event.model_id) ? (
            <Text dimColor>  Model: {event.model_id}</Text>
          ) : null}
          {('provider' in event && event.provider) ? (
            <Text dimColor>  Provider: {event.provider}</Text>
          ) : null}
          
          {/* Memory Configuration */}
          {('memory' in event && event.memory) ? (
            <>
              <Text dimColor>  Memory backend: {event.memory.backend || 'unknown'}</Text>
              {event.memory.has_existing ? (
                <Text dimColor>  Previous memories detected - will be loaded</Text>
              ) : null}
              {event.memory.total_count ? (
                <Text dimColor>  Existing memories: {event.memory.total_count}</Text>
              ) : null}
              
              {/* Memory Categories */}
              {event.memory.categories && Object.keys(event.memory.categories).length > 0 ? (
                <Text dimColor>  Memory categories: {Object.entries(event.memory.categories).map(([category, count]) => `${category}:${count}`).join(', ')}</Text>
              ) : null}
              
              {/* Recent Findings Summary */}
              {event.memory.recent_findings && Array.isArray(event.memory.recent_findings) && event.memory.recent_findings.length > 0 ? (
                <Text dimColor>  Recent findings: {event.memory.recent_findings.length} items available</Text>
              ) : null}
            </>
          ) : null}
          
          {/* Environment Info */}
          {('ui_mode' in event && event.ui_mode) ? (
            <Text dimColor>  UI Mode: {event.ui_mode}</Text>
          ) : null}
          {('observability' in event) && (
            <Text dimColor>  Observability: {event.observability ? 'enabled' : 'disabled'}</Text>
          )}
          {('tools_available' in event && event.tools_available) ? (
            <Text dimColor>  Available Tools: {event.tools_available}</Text>
          ) : null}
        </Box>
      );

    case 'evaluation_step_complete': {
      const status = String((event as any).status || 'completed');
      if (status === 'completed') return null;
      const metric = String((event as any).evaluation_metric || '').replace(/_/g, ' ');
      const kind = String((event as any).evaluation_step_kind || 'evaluation').replace(/_/g, ' ');
      const scope = String((event as any).evaluation_scope || 'operation');
      const label = metric ? `${scope}: ${metric}` : `${scope}: ${kind}`;
      const message = String((event as any).message || 'Evaluation step did not complete');
      return (
        <Box flexDirection="column" marginTop={1}>
          <Text color={status === 'failed' ? 'red' : 'yellow'} bold>
            [RAGAS EVALUATION] {label} {status}
          </Text>
          <Box marginLeft={2}>
            <Text dimColor>└─ {message}</Text>
          </Box>
        </Box>
      );
    }

    case 'evaluation_complete': {
      const status = String((event as any).status || ((event as any).success === false ? 'failed' : 'completed'));
      const scores = ((event as any).scores && typeof (event as any).scores === 'object')
        ? Object.entries((event as any).scores).filter(([, value]) => typeof value === 'number') as Array<[string, number]>
        : [];
      const average = typeof (event as any).average_score === 'number'
        ? (event as any).average_score
        : (scores.length > 0 ? scores.reduce((sum, [, value]) => sum + value, 0) / scores.length : null);
      if (status !== 'completed') {
        return (
          <Box flexDirection="column" marginTop={1}>
            <Text color={status === 'failed' ? 'red' : 'yellow'} bold>
              EVALUATION {status === 'no_results' ? 'COMPLETED WITHOUT RESULTS' : 'FAILED'}
            </Text>
            {(event as any).message && <Text dimColor>  {(event as any).message}</Text>}
          </Box>
        );
      }
      return (
        <Box flexDirection="column" marginTop={1}>
          <Text color="green" bold>EVALUATION COMPLETE</Text>
          <Text dimColor>
            {'  '}Metrics: {scores.length}{average != null ? ` | Average: ${(average * 100).toFixed(1)}%` : ''}
          </Text>
          {scores.map(([name, value], index) => (
            <Text key={name} dimColor>
              {'  '}{index === scores.length - 1 ? '└─' : '├─'} {name.replace(/_/g, ' ')}: {(value * 100).toFixed(1)}%
            </Text>
          ))}
        </Box>
      );
    }

    case 'specialist_start': {
      const specialist = event.specialist || 'validation';
      const task = event.task || 'Sub-agent analysis';
      const finding = event.finding ? `"${event.finding}"` : null;
      const artifactCount = event.artifactPaths?.length || 0;

      return (
        <Box flexDirection="column" marginTop={1}>
          <Box>
            <Text color="yellow">[SUB-AGENT] </Text>
            <Text>{specialist}_specialist</Text>
          </Box>
          <Box marginLeft={2}>
            <Text dimColor>└─ task: </Text>
            <Text>{task}</Text>
          </Box>
          {finding && (
            <Box marginLeft={2}>
              <Text dimColor>└─ finding: </Text>
              <Text>{finding}</Text>
            </Box>
          )}
          {artifactCount > 0 && (
            <Box marginLeft={2}>
              <Text dimColor>└─ artifacts: </Text>
              <Text color="cyan">{artifactCount} files</Text>
            </Box>
          )}
        </Box>
      );
    }

    case 'specialist_progress': {
      const status = event.status || 'Processing';
      const gate = event.gate;
      const totalGates = event.totalGates;
      const tool = event.tool;

      let progressText = status;
      if (gate && totalGates) {
        progressText = `Gate ${gate}/${totalGates}: ${status}`;
      } else if (tool) {
        progressText = `${tool} - ${status}`;
      }

      return (
        <Box marginLeft={2} marginTop={1}>
          <Text dimColor>⏳ </Text>
          <Text dimColor>{progressText}</Text>
        </Box>
      );
    }

    case 'specialist_end': {
      const specialist = event.specialist || 'validation';
      const result = event.result || {};
      const validationStatus = result.validationStatus || result.validation_status || 'unknown';
      const confidence = result.confidence;
      const severityMax = result.severityMax || result.severity_max;
      const failedGates = result.failedGates || result.failed_gates || [];

      // Determine icon and color based on status
      let statusIcon = '✓';
      let statusColor = 'green';
      if (validationStatus === 'hypothesis' || validationStatus === 'unverified') {
        statusIcon = '⚠';
        statusColor = 'yellow';
      } else if (validationStatus === 'error') {
        statusIcon = '✗';
        statusColor = 'red';
      }

      return (
        <Box flexDirection="column" marginTop={1}>
          <Box>
            <Text color={statusColor}>{statusIcon} </Text>
            <Text>{specialist}_specialist </Text>
            <Text dimColor>→ </Text>
            <Text color={statusColor} bold>{validationStatus}</Text>
            {confidence !== undefined && (
              <>
                <Text dimColor> | confidence: </Text>
                <Text color="cyan">{confidence}%</Text>
              </>
            )}
            {severityMax && (
              <>
                <Text dimColor> | severity: </Text>
                <Text color="yellow">{severityMax}</Text>
              </>
            )}
          </Box>
          {Array.isArray(failedGates) && failedGates.length > 0 && (
            <Box marginLeft={2}>
              <Text color="red">└─ failed gates: </Text>
              <Text dimColor>[{failedGates.join(', ')}]</Text>
            </Box>
          )}
        </Box>
      );
    }

    default:
      return null;
  }
});

// Memoize EventLine component for performance
const MemoizedEventLine = React.memo(EventLine);

// Shared helper to compute normalized, grouped display items from events
type DisplayGroup = {
  type: 'reasoning_group' | 'single';
  events: DisplayStreamEvent[];
  startIdx: number;
};

/**
 * Simplifies event grouping now that backend handles deduplication.
 * Only groups consecutive reasoning events for visual presentation.
 */
export const computeDisplayGroups = (events: DisplayStreamEvent[]): DisplayGroup[] => {
  // Flatten any batch events first
  const flattened: DisplayStreamEvent[] = [];
  
  for (const event of events) {
    if (event.type === 'batch' && 'events' in event && Array.isArray(event.events)) {
      // Expand batch events
      flattened.push(...event.events);
    } else {
      flattened.push(event);
    }
  }
  
  // Group consecutive reasoning events for cleaner display
  const groups: DisplayGroup[] = [];
  let currentReasoningGroup: DisplayStreamEvent[] = [];
  let startIdx = 0;
  
  flattened.forEach((event, idx) => {
    if (event.type === 'reasoning' || event.type === 'reasoning_delta') {
      currentReasoningGroup.push(event);
    } else {
      // Flush any pending reasoning group
      if (currentReasoningGroup.length > 0) {
        groups.push({
          type: 'reasoning_group',
          events: currentReasoningGroup,
          startIdx
        });
        currentReasoningGroup = [];
      }
      
      // Add non-reasoning event as single
      groups.push({
        type: 'single',
        events: [event],
        startIdx: idx
      });
      startIdx = idx + 1;
    }
  });
  
  // Flush final reasoning group if any
  if (currentReasoningGroup.length > 0) {
    groups.push({
      type: 'reasoning_group',
      events: currentReasoningGroup,
      startIdx
    });
  }
  
  return groups;
};


export const StreamDisplay: React.FC<StreamDisplayProps> = React.memo(({ events, animationsEnabled = true }) => {
  // Track tool inputs (for handling streamed tool_input_update events)
  const [toolInputs, setToolInputs] = React.useState<Map<string, any>>(new Map());
  
  // Resolve output base directory from config so report resolution matches backend outputDir
  const { config } = useConfig();
  const outputBaseDir = React.useMemo(() => {
    try {
      const raw = config.outputDir || './outputs';
      if (path.isAbsolute(raw)) {
        return raw;
      }
      const base = resolveProjectRoot() ?? process.cwd();
      return path.resolve(base, raw);
    } catch {
      return path.resolve(process.cwd(), 'outputs');
    }
  }, [config.outputDir]);
  
  React.useEffect(() => {
    const newToolInputs = new Map<string, any>();
    
    events.forEach(event => {
      if (event.type === 'tool_start') {
        // Store initial tool input only if it has meaningful content
        if ('tool_id' in event && event.tool_id) {
          const ti: any = (event as any).tool_input;
          const hasMeaningful = (() => {
            if (ti == null) return false;
            if (typeof ti === 'string') return ti.trim().length > 0;
            if (Array.isArray(ti)) return ti.length > 0;
            if (typeof ti === 'object') return Object.keys(ti).length > 0;
            return !!ti;
          })();
          if (hasMeaningful) {
            newToolInputs.set(event.tool_id, ti);
          }
        }
      } else if (event.type === 'tool_input_update' || event.type === 'tool_input_corrected') {
        // Update tool input with complete/corrected data
        if ('tool_id' in event && event.tool_id && 'tool_input' in event) {
          newToolInputs.set(event.tool_id, event.tool_input);
        }
      }
    });
    
    setToolInputs(newToolInputs);
  }, [events]);
  
  // Group consecutive reasoning events to prevent multiple labels
  const displayGroups = React.useMemo(() => computeDisplayGroups(events), [events]);
  const terminationDetail = React.useMemo(() => latestTerminationDetail(events), [events]);
  
  const projectRoot = React.useMemo(() => resolveProjectRoot(), []);

  const reportDetails = React.useMemo(
    () => deriveReportDetails(events, outputBaseDir),
    [events, outputBaseDir]
  );

  const operationContext = React.useMemo(
    () => deriveOperationContext(events, reportDetails.path),
    [events, reportDetails.path]
  );

  // Soft virtualization: render only the most recent N groups to cap Ink/Yoga node count
  // This dramatically reduces memory pressure after long sessions while preserving recent context.
  const MAX_GROUPS_RENDERED = (() => {
    const env = Number(process.env.CYBER_MAX_GROUPS_RENDERED);
    if (Number.isFinite(env) && env > 50) return Math.floor(env);
    // Default cap chosen to balance usability and stability
    return 500;
  })();
  const totalGroups = displayGroups.length;

  // Ensure that the FINAL REPORT header (if present) is always kept in view
  let startIndex = Math.max(0, totalGroups - MAX_GROUPS_RENDERED);
  let finalReportIndex = -1;
  for (let i = 0; i < totalGroups; i++) {
    const group = displayGroups[i];
    if (group.events.some(e => e.type === 'progress_update' && (e as any).step === 'FINAL REPORT')) {
      finalReportIndex = i;
      break;
    }
  }
  if (finalReportIndex >= 0 && finalReportIndex < startIndex) {
    startIndex = finalReportIndex;
  }

  const groupsToRender = displayGroups.slice(startIndex);
  const omittedCount = startIndex;

  return (
    <Box flexDirection="column">
      {/* Omitted items banner to indicate truncated history in the viewport */}
      {omittedCount > 0 && (
        <Box marginBottom={1}>
          <Text dimColor>… {omittedCount} earlier items omitted (press up or use logs to review)</Text>
        </Box>
      )}
      
      {groupsToRender.map((group, idx) => {
        if (group.type === 'reasoning_group') {
          // Display reasoning group with single label - memoize content combination
          const combinedContent = group.events.reduce((acc, e) => {
            // Prefer full reasoning content when present
            if ('content' in e && (e as any).content) {
              const content = (e as any).content;
              // Add spacing between chunks if accumulator is not empty and doesn't end with whitespace
              if (acc && !acc.endsWith(' ') && !acc.endsWith('\n')) {
                return acc + ' ' + content;
              }
              return acc + content;
            }
            // Also accumulate streaming deltas so we don't lose interim reasoning
            if (e.type === 'reasoning_delta' && 'delta' in (e as any) && (e as any).delta) {
              const delta = (e as any).delta;
              if (acc && !acc.endsWith(' ') && !acc.endsWith('\n')) {
                return acc + ' ' + delta;
              }
              return acc + delta;
            }
            return acc;
          }, '');

          const agentName = group.events[0] && 'agent_name' in group.events[0]
              ? (group.events[0] as any).agent_name
            : null;

          const reasoningLabel = agentName
              ? `reasoning (${agentName})`
            : 'reasoning';
          
          return (
            <Box key={`reasoning-group-${group.startIdx}`} flexDirection="column" marginTop={1}>
              <Text color="cyan" bold>{reasoningLabel}</Text>
              <Box paddingLeft={0}>
                <Text color="cyan" wrap="wrap">{combinedContent}</Text>
              </Box>
            </Box>
          );
        } else {
          // Display single events normally
          return group.events.map((event, i) => (
              <MemoizedEventLine 
                key={event.id || `ev-${startIndex + idx}-${i}`}  // Use event ID if available
                event={event} 
                toolInputs={toolInputs} 
                animationsEnabled={animationsEnabled} 
                operationContext={operationContext}
                reportPath={reportDetails.path}
                reportFallbackContent={reportDetails.content}
                projectRoot={projectRoot}
                terminationDetail={terminationDetail}
                enableInlineReportView={true}
              />
          ));
        }
      })}
    </Box>
  );
});

export const StaticStreamDisplay: React.FC<{
  events: DisplayStreamEvent[];
  terminalWidth?: number;
  availableHeight?: number;
}> = React.memo(({ events, terminalWidth, availableHeight }) => {
  const eventKeyMapRef = React.useRef<WeakMap<object, string>>(new WeakMap());
  const eventSeqRef = React.useRef(0);
  const stableEventKey = React.useCallback((event: DisplayStreamEvent): string => {
    const any = event as any;
    const explicit = any?.id ?? any?.toolId ?? any?.tool_id;
    if (explicit) return `${String(any?.type ?? 'event')}-${String(explicit)}`;
    if (any?.timestamp) return `${String(any?.type ?? 'event')}-${String(any.timestamp)}`;
    if (event && typeof event === 'object') {
      const existing = eventKeyMapRef.current.get(event as object);
      if (existing) return existing;
      const next = `${String(any?.type ?? 'event')}-seq-${eventSeqRef.current++}`;
      eventKeyMapRef.current.set(event as object, next);
      return next;
    }
    return `event-seq-${eventSeqRef.current++}`;
  }, []);
  const groups = React.useMemo(() => computeDisplayGroups(events), [events]);
  const terminationDetail = React.useMemo(() => latestTerminationDetail(events), [events]);
  const projectRoot = React.useMemo(() => resolveProjectRoot(), []);

  // Resolve output base directory from config for consistent path mapping
  const { config } = useConfig();
  const outputBaseDir = React.useMemo(() => {
    try {
      const raw = config.outputDir || './outputs';
      if (path.isAbsolute(raw)) {
        return raw;
      }
      const base = projectRoot ?? process.cwd();
      return path.resolve(base, raw);
    } catch {
      return path.resolve(process.cwd(), 'outputs');
    }
  }, [config.outputDir, projectRoot]);

  const reportDetails = React.useMemo(
    () => deriveReportDetails(events, outputBaseDir),
    [events, outputBaseDir]
  );

  const operationContext = React.useMemo(
    () => deriveOperationContext(events, reportDetails.path),
    [events, reportDetails.path]
  );

  // Flatten groups into discrete render items with stable keys.
  type Item = { key: string; render: () => React.ReactNode };
  const items: Item[] = React.useMemo(() => {
    const out: Item[] = [];
    groups.forEach((group) => {
      if (group.type === 'reasoning_group') {
        // Use reduce for better performance with large arrays
        const combinedContent = group.events.reduce((acc, e) => {
          // Prefer full reasoning content when present
          if ('content' in e && (e as any).content) {
            const content = (e as any).content;
            // Add spacing between chunks if accumulator is not empty and doesn't end with whitespace
            if (acc && !acc.endsWith(' ') && !acc.endsWith('\n')) {
              return acc + ' ' + content;
            }
            return acc + content;
          }
          // Also accumulate streaming deltas so we don't lose interim reasoning
          if (e.type === 'reasoning_delta' && 'delta' in (e as any) && (e as any).delta) {
            const delta = (e as any).delta;
            if (acc && !acc.endsWith(' ') && !acc.endsWith('\n')) {
              return acc + ' ' + delta;
            }
            return acc + delta;
          }
          return acc;
        }, '');

        const agentName = group.events[0] && 'agent_name' in group.events[0]
            ? (group.events[0] as any).agent_name
          : null;

        const reasoningLabel = agentName
            ? `reasoning (${agentName})`
          : 'reasoning';
        
        const key = `rg-${group.startIdx}-${stableEventKey(group.events[0])}`;
        out.push({
          key,
          render: () => (
            <Box key={key} flexDirection="column" marginTop={1}>
              <Text color="cyan" bold>{reasoningLabel}</Text>
              <Box paddingLeft={0}>
                <Text color="cyan" wrap="wrap">{combinedContent}</Text>
              </Box>
            </Box>
          )
        });
      } else {
        group.events.forEach((event, i) => {
          const key = `ev-${group.startIdx}-${i}-${stableEventKey(event)}`;
          out.push({
            key,
            render: () => (
              <MemoizedEventLine
                key={key}
                event={event}
                animationsEnabled={false}
                operationContext={operationContext}
                reportPath={reportDetails.path}
                reportFallbackContent={reportDetails.content}
                projectRoot={projectRoot}
                terminationDetail={terminationDetail}
                // Disable InlineReportViewer here; the dynamic StreamDisplay path will
                // render the inline preview once the report is fully available.
                enableInlineReportView={false}
              />
            )
          });
        });
      }
    });
    return out;
  }, [
    groups,
    operationContext,
    reportDetails.path,
    reportDetails.content,
    projectRoot,
    stableEventKey,
    terminationDetail,
  ]);

  return (
    <Static key={items[0]?.key ?? 'empty'} items={items}>
      {(item: Item) => item.render()}
    </Static>
  );
});
