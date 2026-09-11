/**
 * TestExecutionService - emits synthetic events for integration testing
 * Enabled when CYBER_TEST_MODE=true and CYBER_TEST_EXECUTION=mock
 */

import { EventEmitter } from 'events';
import { ExecutionService, ExecutionMode, ValidationResult, ExecutionHandle, ExecutionResult, ExecutionCapabilities } from './ExecutionService.js';
import fs from 'fs';

export class TestExecutionService extends EventEmitter implements ExecutionService {
  private active = false;
  private intervalHandle: NodeJS.Timeout | null = null;
  private peakRssBytes = 0;
  private peakHeapUsedBytes = 0;

  getMode(): ExecutionMode {
    return ExecutionMode.PYTHON_CLI;
  }

  getCapabilities(): ExecutionCapabilities {
    return {
      canExecute: true,
      supportsStreaming: true,
      supportsParallel: false,
      maxConcurrent: 1,
      requirements: ['Test mode only']
    };
  }

  async isSupported(): Promise<boolean> {
    return true;
  }

  async validate(): Promise<ValidationResult> {
    return { valid: true, issues: [], warnings: [] };
  }

  async setup(_config: any, onProgress?: (message: string) => void): Promise<void> {
    onProgress?.('Test execution service ready');
  }

  async execute(_params: any, _config: any): Promise<ExecutionHandle> {
    this.active = true;
    this.peakRssBytes = 0;
    this.peakHeapUsedBytes = 0;
    const startTime = Date.now();

    const events = this.loadEvents();
    const intervalMs = this.getEventIntervalMs();

    // Emit started
    setTimeout(() => this.emit('started', { id: 'test' } as any), 10);

    // Stream events with small delay
    let idx = 0;
    this.intervalHandle = setInterval(() => {
      if (!this.active) return;
      if (idx >= events.length) {
        if (this.intervalHandle) {
          clearInterval(this.intervalHandle);
          this.intervalHandle = null;
        }
        this.active = false;
        this.sampleMemory();
        this.writeMemoryLog(Date.now() - startTime);
        this.emit('complete', { success: true, durationMs: Date.now() - startTime } as ExecutionResult);
        return;
      }
      const e = events[idx++];
      this.sampleMemory();
      // Normalize minimal differences (support both tool_input and args for compatibility)
      this.emit('event', e);
    }, intervalMs);

    const handle: ExecutionHandle = {
      id: `test-${startTime}`,
      result: new Promise<ExecutionResult>((resolve) => {
        this.once('complete', (res: ExecutionResult) => resolve(res));
      }),
      stop: async () => {
        if (this.intervalHandle) {
          clearInterval(this.intervalHandle);
          this.intervalHandle = null;
        }
        this.active = false;
        this.emit('stopped');
      },
      isActive: () => this.active,
    };

    return handle;
  }

  cleanup(): void {
    if (this.intervalHandle) {
      try { clearInterval(this.intervalHandle); } catch {}
      this.intervalHandle = null;
    }
    this.removeAllListeners();
    this.active = false;
  }

  async stop(): Promise<void> {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle);
      this.intervalHandle = null;
    }
    if (this.active) {
      this.active = false;
      this.emit('stopped');
    }
  }

  isActive(): boolean {
    return this.active;
  }

  private loadEvents(): any[] {
    try {
      const path = process.env.CYBER_TEST_EVENTS_PATH;
      if (path && fs.existsSync(path)) {
        const content = fs.readFileSync(path, 'utf-8');
        const lines = content.split(/\r?\n/).filter(Boolean);
        return lines.map((line) => JSON.parse(line));
      }
    } catch (e) {
      // ignore and fallback
    }
    // Default event sequence if no file provided
    return [
      { type: 'progress_update', step: 1, progressPercent: 0, operation: 'OP_TEST', duration: '0s' },
      { type: 'reasoning', content: 'Analyzing target for vulnerabilities...' },
      { type: 'tool_start', tool_name: 'shell', tool_input: { command: 'echo hello' } },
      { type: 'output', content: 'hello' },
      { type: 'metrics_update', metrics: { inputTokens: 100, outputTokens: 50, duration: '2s' } },
      { type: 'tool_invocation_end' },
      { type: 'progress_update', step: 2, progressPercent: 50, operation: 'OP_TEST', duration: '2s' },
      { type: 'tool_start', tool_name: 'http_request', tool_input: { method: 'GET', url: 'http://example.com' } },
      { type: 'output', content: 'HTTP/1.1 200 OK' },
      { type: 'tool_invocation_end' },
      { type: 'progress_update', step: 3, progressPercent: 100, operation: 'OP_TEST', duration: '3s' },
      { type: 'output', content: 'Finalizing...' }
    ];
  }

  private getEventIntervalMs(): number {
    const configured = Number(process.env.CYBER_TEST_EVENT_INTERVAL_MS);
    if (Number.isFinite(configured) && configured >= 0) {
      return Math.floor(configured);
    }
    return 60;
  }

  private sampleMemory(): void {
    if (!process.env.CYBER_TEST_MEMORY_LOG_PATH) {
      return;
    }
    try {
      const usage = process.memoryUsage();
      this.peakRssBytes = Math.max(this.peakRssBytes, usage.rss || 0);
      this.peakHeapUsedBytes = Math.max(this.peakHeapUsedBytes, usage.heapUsed || 0);
    } catch {}
  }

  private writeMemoryLog(durationMs: number): void {
    const logPath = process.env.CYBER_TEST_MEMORY_LOG_PATH;
    if (!logPath) {
      return;
    }
    try {
      fs.writeFileSync(logPath, JSON.stringify({
        durationMs,
        peakRssBytes: this.peakRssBytes,
        peakHeapUsedBytes: this.peakHeapUsedBytes,
        peakRssMb: Math.round(this.peakRssBytes / 1024 / 1024),
        peakHeapUsedMb: Math.round(this.peakHeapUsedBytes / 1024 / 1024)
      }, null, 2));
    } catch {}
  }
}
