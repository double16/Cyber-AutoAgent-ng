/**
 * Event Aggregator - Simplified for new reasoning approach
 * Python backend now handles reasoning accumulation and emits complete blocks
 */

import type {DisplayStreamEvent} from '../components/StreamDisplay.js';

export class EventAggregator {
  private outputBuffer: string[] = [];
  private reasoningBuffer: string[] = [];
  private currentToolId?: string;
  private lastEventType?: string;
  private activeThinking: boolean = false;
  private activeReasoningSession: boolean = false;
  private pendingProgressUpdate?: DisplayStreamEvent;
  // Track step gating to properly attribute early reasoning to the previous step
  private pendingStepNumber?: number;
  private hasToolForPendingStep: boolean = false;
  private lastEmittedStepNumber?: number;
  
  // Command buffering delay constant  
  private readonly COMMAND_BUFFER_MS = 100; // Short delay to collect all commands
  
  // Prevent duplicate outputs
  private lastOutputContent: string = '';
  private outputDedupeTimeMs = 1000; // 1 second window for deduplication
  private lastOutputTime: number = 0;
  
  // Dedupe: avoid duplicate tool headers from dual emitters (hooks + bridge handler)
  private displayedToolStartIds: Set<string> = new Set();
  // Track dedupe keys by tool id so we can clean them up on tool_end
  private toolStartDedupeKeyById: Map<string, string> = new Map();
  
  // These methods are no longer used since we don't buffer events
  // Kept for potential future use if buffering is needed
  hasPendingEvents(): boolean {
    return false;
  }
  
  flushPendingEvents(): DisplayStreamEvent[] {
    return [];
  }
  
  flush(): DisplayStreamEvent[] {
    return [];
  }
  
  processEvent(event: any): DisplayStreamEvent[] {
    const results: DisplayStreamEvent[] = [];
    
    switch (event.type) {
      case 'operation_init':
            // Reset all internal state for new operation to prevent unbounded growth
            this.displayedToolStartIds.clear();
            this.toolStartDedupeKeyById.clear();
            this.outputBuffer = [];
            this.reasoningBuffer = [];
            this.currentToolId = undefined;
            this.lastEventType = undefined;
            this.activeThinking = false;
            this.activeReasoningSession = false;
            this.pendingProgressUpdate = undefined;
            this.pendingStepNumber = undefined;
            this.hasToolForPendingStep = false;
            this.lastEmittedStepNumber = undefined;
            this.lastOutputContent = '';
            this.lastOutputTime = 0;
            break;

      case 'progress_update':
        // End any active reasoning session
        this.activeReasoningSession = false;
        if (
          (event as any).operation_stage === 'final_report' ||
          (event as any).operation_stage === 'ragas_evaluation'
        ) {
          results.push({
            type: 'progress_update',
            step: event.step,
            progressPercent: (event as any).progressPercent,
            operation: event.operation,
            duration: event.duration,
            operation_stage: (event as any).operation_stage,
            report_step_index: (event as any).report_step_index,
            report_step_total: (event as any).report_step_total,
            report_step_kind: (event as any).report_step_kind,
            report_step_label: (event as any).report_step_label,
            evaluation_step_index: (event as any).evaluation_step_index,
            evaluation_step_total: (event as any).evaluation_step_total,
            evaluation_step_kind: (event as any).evaluation_step_kind,
            evaluation_scope: (event as any).evaluation_scope,
            evaluation_metric: (event as any).evaluation_metric,
            evaluation_step_label: (event as any).evaluation_step_label,
          } as DisplayStreamEvent);
          break;
        }
        // Buffer the progress update; flush when the first tool event of this step arrives
        this.pendingProgressUpdate = {
          type: 'progress_update',
          step: event.step,
          progressPercent: (event as any).progressPercent,
          operation: event.operation,
          duration: event.duration,
          agent_run_id: (event as any).agent_run_id,
          agent_name: (event as any).agent_name,
          agent_type: (event as any).agent_type,
          parent_agent_run_id: (event as any).parent_agent_run_id,
          agent_sub_step: (event as any).agent_sub_step,
          agent_total_actions: (event as any).agent_total_actions,
        } as DisplayStreamEvent;
        // Track pending step number and reset tool flag
        this.pendingStepNumber = (typeof event.step === 'number') ? event.step : undefined;
        this.hasToolForPendingStep = false;
        this.activeReasoningSession = false;
        break;
        
      case 'reasoning':
        // Python backend now sends complete reasoning blocks - no buffering needed
        if (event.content && typeof event.content === 'string' && event.content.trim()) {
          // Clear any active thinking animations when reasoning is shown
          if (this.activeThinking) {
            results.push({ type: 'thinking_end' } as DisplayStreamEvent);
            this.activeThinking = false;
          }
          
          // Start reasoning session
          this.activeReasoningSession = true;

          // Emit the complete reasoning block directly, preserving agent context if present
          const reasoningEvent: any = {
            type: 'reasoning',
            content: (event.content as string).trim(),
          };
          reasoningEvent.agent_run_id = (event as any).agent_run_id;
          reasoningEvent.agent_name = (event as any).agent_name;
          reasoningEvent.agent_type = (event as any).agent_type;
          reasoningEvent.parent_agent_run_id = (event as any).parent_agent_run_id;
          
          // IMPORTANT: If a new progress update is pending and no tool has been seen for that step yet,
          // keep this reasoning attached to the previous step by not flushing the header here.
          // This preserves the intuitive attribution (reasoning summarizing prior step results).
          results.push(reasoningEvent as DisplayStreamEvent);
        }
        break;
        
      case 'reasoning_delta':
        // Legacy reasoning delta events - no longer used since Python sends complete blocks
        // Ignore these events
        break;
        
      case 'thinking':
        // Handle thinking start without conflicting with reasoning
        if (!this.activeReasoningSession && !this.activeThinking) {
          this.activeThinking = true;
          results.push({
            type: 'thinking',
            context: event.context,
            startTime: event.startTime,
            metadata: event.metadata
          } as DisplayStreamEvent);
        }
        break;
        
      case 'thinking_end':
        if (this.activeThinking) {
          this.activeThinking = false;
          results.push({
            type: 'thinking_end'
          } as DisplayStreamEvent);
        }
        break;
        
      case 'delayed_thinking_start':
        // Handle delayed thinking start - pass through and mark as active
        if (!this.activeThinking && !this.activeReasoningSession) {
          this.activeThinking = true; // Mark as active so it can be stopped later
          results.push(event as DisplayStreamEvent);
        }
        break;
        
      case 'tool_start':
        // Clear any active thinking when tool starts
        if (this.activeThinking) {
          results.push({ type: 'thinking_end' } as DisplayStreamEvent);
          this.activeThinking = false;
        }

        // If there is a pending progress update, emit it now before the tool header
        if (this.pendingProgressUpdate) {
          results.push(this.pendingProgressUpdate);
          // Update step gating state
          this.lastEmittedStepNumber = this.pendingStepNumber ?? this.lastEmittedStepNumber;
          this.pendingProgressUpdate = undefined;
          this.hasToolForPendingStep = true;
        }

        // Dedupe duplicate tool headers by tool id (hooks + bridge may emit both)
        {
          const candidateId = (event as any).toolId ?? (event as any).tool_id ?? undefined;
          const stepKey = (this.pendingStepNumber ?? this.lastEmittedStepNumber ?? 0).toString();
          if (candidateId) {
            const dedupeKey = `${stepKey}:${candidateId}`;
            if (this.displayedToolStartIds.has(dedupeKey)) {
              // Already displayed a header for this tool invocation within this step; ignore duplicates
              break;
            }
            this.displayedToolStartIds.add(dedupeKey);
            this.toolStartDedupeKeyById.set(String(candidateId), dedupeKey);
          }
        }
        
        this.currentToolId = event.toolId;

        results.push({
          type: 'tool_start',
          tool_name: event.toolName || event.tool_name || '',
          tool_input: event.args || event.tool_input || {},
          toolId: event.toolId,
          toolName: event.toolName,
          agent_run_id: (event as any).agent_run_id,
          agent_name: (event as any).agent_name,
          agent_type: (event as any).agent_type,
          parent_agent_run_id: (event as any).parent_agent_run_id,
        } as DisplayStreamEvent);
        break;
        
      case 'shell_command':
        // Treat shell_command as evidence of a tool starting (in case a start event was missed)
        if (this.pendingProgressUpdate && !this.hasToolForPendingStep) {
          results.push(this.pendingProgressUpdate);
          this.lastEmittedStepNumber = this.pendingStepNumber ?? this.lastEmittedStepNumber;
          this.pendingProgressUpdate = undefined;
          this.hasToolForPendingStep = true;
        }
        results.push({
          type: 'shell_command',
          command: event.command,
          toolId: this.currentToolId,
          id: `shell_${Date.now()}`,
          timestamp: new Date().toISOString(),
          sessionId: 'current'
        } as DisplayStreamEvent);
        
        // Start thinking animation after commands are shown (use delayed event)
        if (!this.activeThinking && !this.activeReasoningSession) {
          results.push({
            type: 'delayed_thinking_start',
            context: 'tool_execution',
            startTime: Date.now(),
            delay: this.COMMAND_BUFFER_MS
          } as DisplayStreamEvent);
        }
        break;
        
      case 'output':
        // Handle tool output or general output
        if (event.content) {
          // Basic deduplication
          const currentTime = Date.now();
          if (event.content === this.lastOutputContent && 
              currentTime - this.lastOutputTime < this.outputDedupeTimeMs) {
            break; // Skip duplicate
          }
          this.lastOutputContent = event.content;
          this.lastOutputTime = currentTime;
          
          // IMPORTANT: Do NOT flush a pending progress update on generic 'output' events.
          // Late output from the previous tool can arrive after a new progress_update
          // (e.g., final buffer flush). Flushing here would incorrectly advance
          // the header before prior-step reasoning is rendered.
          // We only flush on explicit tool signals (tool_start/tool_output).
          
          // Clear any active thinking when output appears
          if (this.activeThinking) {
            results.push({ type: 'thinking_end' } as DisplayStreamEvent);
            this.activeThinking = false;
          }
          
          results.push({
            type: 'output',
            content: event.content,
            toolId: this.currentToolId
          } as DisplayStreamEvent);
        }
        break;
        
      case 'tool_output':
        // If a step is pending and we receive a standardized tool_output event,
        // flush the progress update before displaying output to keep attribution correct.
        if (this.pendingProgressUpdate && !this.hasToolForPendingStep) {
          results.push(this.pendingProgressUpdate);
          this.lastEmittedStepNumber = this.pendingStepNumber ?? this.lastEmittedStepNumber;
          this.pendingProgressUpdate = undefined;
          this.hasToolForPendingStep = true;
        }
        // Pass through the event as-is for StreamDisplay to render
        results.push(event as DisplayStreamEvent);
        break;
        
      case 'tool_end':
        // Clear any active thinking when tool ends
        if (this.activeThinking) {
          results.push({ type: 'thinking_end' } as DisplayStreamEvent);
          this.activeThinking = false;
        }

        // Cleanup dedupe cache for this tool id to avoid unbounded growth
        if ((event as any).toolId) {
          const tid = String((event as any).toolId);
          const key = this.toolStartDedupeKeyById.get(tid);
          if (key) {
            this.displayedToolStartIds.delete(key);
            this.toolStartDedupeKeyById.delete(tid);
          } else {
            // Fallback: remove by raw id if present (legacy cleanup)
            this.displayedToolStartIds.delete(tid);
          }
        }
        
        results.push({
          type: 'tool_end',
          toolId: event.toolId,
          tool: event.toolName || 'unknown',
          success: event.success,
          outcome: event.outcome,
          executed: event.executed,
          id: `tool_end_${Date.now()}`,
          timestamp: new Date().toISOString(),
          sessionId: 'current'
        } as DisplayStreamEvent);
        this.currentToolId = undefined;
        break;
        
      case 'operation_complete':
        // Clear any active states
        this.activeThinking = false;
        this.activeReasoningSession = false;
        
        results.push({
          type: 'metrics_update',
          metrics: event.metrics || {},
          duration: event.duration
        } as DisplayStreamEvent);
        break;
        
      default:
        // Pass through other events as-is
        results.push(event as DisplayStreamEvent);
        break;
    }
    
    this.lastEventType = event.type;
    return results;
  }
}
