/**
 * Operation Management Service
 * Handles operation lifecycle, progress tracking, cost monitoring, and model switching
 * Now uses configurable pricing from ConfigContext instead of hardcoded values
 */

import { Config } from '../contexts/ConfigContext.js';
import { loggingService } from './LoggingService.js';
import { peekAllModels, loadAllModels, getPricingPer1kSync, getPricingPer1k, getContextLimitSync, getContextLimit } from './ModelsCatalog.js';

export interface Operation {
  id: string;
  module: string;
  target: string;
  objective: string;
  startTime: Date;
  endTime?: Date;
  currentStep: number;
  totalSteps: number;
  status: 'running' | 'paused' | 'completed' | 'error' | 'cancelled';
  description: string;
  findings: number;
  logs: OperationLog[];
  cost: CostInfo;
  model: string;
  continueOperation?: string | boolean;
  reportOnly?: string | boolean;
}

export interface OperationLog {
  timestamp: Date;
  level: 'info' | 'warning' | 'error' | 'success';
  message: string;
  tool?: string;
  step?: number;
}

export interface CostInfo {
  tokensUsed: number;
  estimatedCost: number;
  inputTokens: number;
  outputTokens: number;
  /** Cache read tokens from prompt caching (75% cheaper than input) */
  cacheReadTokens: number;
  /** Cache write tokens from prompt caching (25% more expensive than input) */
  cacheWriteTokens: number;
  modelPricing: {
    inputCostPer1k: number;
    outputCostPer1k: number;
    /** Cost per 1k cache read tokens (typically ~25% of input cost) */
    cacheReadCostPer1k: number;
    /** Cost per 1k cache write tokens (typically ~125% of input cost) */
    cacheWriteCostPer1k: number;
  };
}

export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  inputCostPer1k: number;
  outputCostPer1k: number;
  contextLimit: number;
  isAvailable: boolean;
}

export class OperationManager {
  private operations: Map<string, Operation> = new Map();
  private currentOperation: Operation | null = null;
  private config: Config;
  private sessionCost: CostInfo = {
    tokensUsed: 0,
    estimatedCost: 0,
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    modelPricing: { inputCostPer1k: 0, outputCostPer1k: 0, cacheReadCostPer1k: 0, cacheWriteCostPer1k: 0 }
  };

  constructor(config: Config) {
    this.config = config;
    // Load session data if available
    this.loadSessionData();
  }

  // Get available models using models.dev (fallback to snapshot), with config.modelPricing as override source
  private getAvailableModelsFromConfig(): ModelInfo[] {
    // NOTE: This method is used synchronously by UI code. Since our models.dev
    // loader is async, we return cached results if already loaded; otherwise, we
    // provide a minimal list derived from config.modelPricing and schedule an
    // async refresh so subsequent calls get enriched data.
    const modelsFromPricing: ModelInfo[] = [];
    if (this.config.modelPricing) {
      Object.entries(this.config.modelPricing).forEach(([modelId, pricing]) => {
        let provider = 'bedrock';
        if (modelId.includes(':') && !modelId.includes('.')) provider = 'ollama';
        else if (modelId.startsWith('bedrock/') || modelId.startsWith('openai/')) provider = 'litellm';
        modelsFromPricing.push({
          id: modelId,
          name: this.getModelDisplayName(modelId),
          provider,
          inputCostPer1k: this.config.modelProvider === 'ollama' ? 0 : pricing.inputCostPer1k,
          outputCostPer1k: this.config.modelProvider === 'ollama' ? 0 : pricing.outputCostPer1k,
          contextLimit: this.getModelContextLimit(modelId),
          isAvailable: true,
        });
      });
    }

    // Try to use the models.dev catalog if available (loaded asynchronously)
    try {
      const peek = peekAllModels();
      // Fire-and-forget async load for future calls
      void loadAllModels().catch(() => {});
      if (peek && peek.length) {
        return peek.map(entry => ({
          id: entry.model.id,
          name: entry.model.name || entry.model.id,
          provider: entry.provider,
          inputCostPer1k: this.config.modelProvider === 'ollama' ? 0 : (entry.model.cost?.input ?? 0),
          outputCostPer1k: this.config.modelProvider === 'ollama' ? 0 : (entry.model.cost?.output ?? (entry.model.cost?.input ?? 0)),
          contextLimit: entry.model.limit?.context ?? 8000,
          isAvailable: true,
        }));
      }
    } catch {
      // ignore catalog errors; use pricing-derived list only
    }

    return modelsFromPricing;
  }

  // Helper to get display names for models
  private getModelDisplayName(modelId: string): string {
    // Prefer models.dev catalog name (synchronous cached lookup first)
    try {
      const peek = peekAllModels();
      if (peek && peek.length) {
        // Exact ID match
        let found = peek.find(entry => entry.model.id === modelId);
        if (!found) {
          // Try last path segment if ID contains '/'
          const shortId = modelId.includes('/') ? modelId.split('/').pop()! : modelId;
          found = peek.find(entry => entry.model.id === shortId || entry.model.name === shortId);
          if (!found) {
            // As a last resort, try suffix/dotted matches
            const dotted = shortId.includes('.') ? shortId.split('.').pop()! : shortId;
            found = peek.find(entry =>
              entry.model.id === dotted ||
              entry.model.id.endsWith(`/${dotted}`) ||
              (entry.model.name ?? '').toLowerCase() === dotted.toLowerCase()
            );
          }
        }
        if (found) {
          return found.model.name || found.model.id;
        }
      }
      // Trigger async population for future calls (non-blocking)
      void loadAllModels().then(() => {}).catch(() => {});
    } catch {
      // ignore catalog errors; fall through to default
    }

    // Fallback: show the raw model id
    return modelId;
  }

  // Helper to get context limits for models
  private getModelContextLimit(modelId: string): number {
    // Try models.dev catalog (synchronous cached lookup first)
    try {
      const cached = getContextLimitSync(modelId);
      if (typeof cached === 'number' && cached > 0) {
        return cached;
      }
      // Trigger async population for future calls
      void getContextLimit(modelId).then(() => {}).catch(() => {});
    } catch {
      // ignore
    }

    // Catalog didn't have it yet (or not loaded); return a conservative default
    return 8000;
  }

  // Start a new operation
  startOperation(
      module: string,
      target: string,
      objective: string,
      model: string,
      continueOperation?: string | boolean,
      reportOnly?: string | boolean,
  ): Operation {
    // Reset session cost for new operation to prevent accumulation across operations
    this.sessionCost = {
      tokensUsed: 0,
      estimatedCost: 0,
      inputTokens: 0,
      outputTokens: 0,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
      modelPricing: this.getModelPricing(model)
    };

    const operation: Operation = {
      id: this.generateOperationId(),
      module,
      target,
      objective,
      startTime: new Date(),
      currentStep: 0,
      totalSteps: 50, // Default, will be updated
      status: 'running',
      description: 'Initializing operation...',
      findings: 0,
      logs: [],
      cost: {
        tokensUsed: 0,
        estimatedCost: 0,
        inputTokens: 0,
        outputTokens: 0,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        modelPricing: this.getModelPricing(model)
      },
      model,
      continueOperation,
      reportOnly,
    };

    this.operations.set(operation.id, operation);
    this.currentOperation = operation;

    this.addLog(operation.id, 'info', `Operation started: ${module} → ${target}`);

    return operation;
  }

  // Update operation progress
  updateProgress(operationId: string, step: number, totalSteps: number, description: string): void {
    const operation = this.operations.get(operationId);
    if (!operation) return;

    operation.currentStep = step;
    operation.totalSteps = totalSteps;
    operation.description = description;
    
    this.addLog(operationId, 'info', `Step ${step}/${totalSteps}: ${description}`);
  }

  // Update operation with partial updates
  updateOperation(operationId: string, updates: Partial<Operation>): void {
    const operation = this.operations.get(operationId);
    if (operation) {
      Object.assign(operation, updates);
      this.operations.set(operationId, operation);
    }
  }

  // Add finding to operation
  addFinding(operationId: string, finding: string): void {
    const operation = this.operations.get(operationId);
    if (!operation) return;

    operation.findings++;
    this.addLog(operationId, 'success', `Finding #${operation.findings}: ${finding}`);
  }

  // Pause operation
  pauseOperation(operationId: string): boolean {
    const operation = this.operations.get(operationId);
    if (!operation || operation.status !== 'running') return false;

    operation.status = 'paused';
    this.addLog(operationId, 'warning', 'Operation paused');
    return true;
  }

  // Resume operation
  resumeOperation(operationId: string): boolean {
    const operation = this.operations.get(operationId);
    if (!operation || operation.status !== 'paused') return false;

    operation.status = 'running';
    this.addLog(operationId, 'info', 'Operation resumed');
    return true;
  }

  // Complete operation
  completeOperation(operationId: string, success: boolean = true): void {
    const operation = this.operations.get(operationId);
    if (!operation) return;

    operation.status = success ? 'completed' : 'error';
    operation.endTime = new Date();
    
    const duration = Math.floor((operation.endTime.getTime() - operation.startTime.getTime()) / 1000);
    this.addLog(operationId, success ? 'success' : 'error', 
      `Operation ${success ? 'completed' : 'failed'} in ${duration}s with ${operation.findings} findings`);

    if (this.currentOperation?.id === operationId) {
      this.currentOperation = null;
    }
  }

  // Switch model during operation
  switchModel(operationId: string, newModel: string): boolean {
    const operation = this.operations.get(operationId);
    if (!operation) return false;

    const oldModel = operation.model;
    operation.model = newModel;
    operation.cost.modelPricing = this.getModelPricing(newModel);
    
    this.addLog(operationId, 'info', `Model switched from ${oldModel} to ${newModel}`);
    return true;
  }

  // Update token usage (with optional cache token support). Values are cumulative.
  updateTokenUsage(
    operationId: string,
    inputTokens: number,
    outputTokens: number,
    cost: number,
    cacheReadTokens: number = 0,
    cacheWriteTokens: number = 0
  ): void {
    const operation = this.operations.get(operationId);
    if (!operation) return;

    if (inputTokens > operation.cost.inputTokens) {
        operation.cost.inputTokens = inputTokens;
    }
    if (outputTokens > operation.cost.outputTokens) {
        operation.cost.outputTokens = outputTokens;
    }
    if (cacheReadTokens > operation.cost.cacheReadTokens) {
        operation.cost.cacheReadTokens = cacheReadTokens;
    }
    if (cacheWriteTokens > operation.cost.cacheWriteTokens) {
        operation.cost.cacheWriteTokens = cacheWriteTokens;
    }
    operation.cost.tokensUsed = operation.cost.inputTokens + operation.cost.outputTokens;
    operation.cost.estimatedCost = cost;
  }

  // Add log entry
  addLog(operationId: string, level: OperationLog['level'], message: string, tool?: string): void {
    const operation = this.operations.get(operationId);
    if (!operation) return;

    operation.logs.push({
      timestamp: new Date(),
      level,
      message,
      tool,
      step: operation.currentStep
    });
  }

  // Get current operation
  getCurrentOperation(): Operation | null {
    return this.currentOperation;
  }

  // Get operation by ID
  getOperation(operationId: string): Operation | null {
    return this.operations.get(operationId) || null;
  }

  // Get all operations
  getAllOperations(): Operation[] {
    return Array.from(this.operations.values());
  }

  // Rename an operation ID to align with backend-provided ID
  // Moves the entry in the map and updates the operation object
  renameOperationId(oldId: string, newId: string): Operation | null {
    if (!oldId || !newId || oldId === newId) return this.operations.get(oldId) || this.operations.get(newId) || null;
    const op = this.operations.get(oldId);
    if (!op) return this.operations.get(newId) || null;
    // Avoid clobbering if newId already exists
    if (this.operations.has(newId)) {
      // If the target ID exists, prefer its object but carry over important fields from the old op
      const target = this.operations.get(newId)!;
      // Merge minimal fields (keep target's identity)
      target.module = op.module || target.module;
      target.target = op.target || target.target;
      target.objective = op.objective || target.objective;
      target.startTime = op.startTime || target.startTime;
      target.currentStep = op.currentStep || target.currentStep;
      target.totalSteps = op.totalSteps || target.totalSteps;
      target.status = op.status || target.status;
      target.description = op.description || target.description;
      target.findings = Math.max(op.findings, target.findings);
      target.logs = op.logs.length > target.logs.length ? op.logs : target.logs;
      this.operations.delete(oldId);
      if (this.currentOperation?.id === oldId) this.currentOperation = target;
      return target;
    }
    // Move op under new key and update id
    this.operations.delete(oldId);
    op.id = newId;
    this.operations.set(newId, op);
    if (this.currentOperation?.id === oldId) this.currentOperation = op;
    return op;
  }

  // Get available models
  getAvailableModels(): ModelInfo[] {
    return this.getAvailableModelsFromConfig();
  }

  // Get model info
  getModelInfo(modelId: string): ModelInfo | null {
    const models = this.getAvailableModelsFromConfig();
    return models.find(m => m.id === modelId) || null;
  }

  // Get operation duration as formatted string
  getOperationDuration(operationId: string): string {
    const operation = this.operations.get(operationId);
    if (!operation) return '0s';
    
    const endTime = operation.endTime || new Date();
    const duration = endTime.getTime() - operation.startTime.getTime();
    
    const seconds = Math.floor(duration / 1000) % 60;
    const minutes = Math.floor(duration / (1000 * 60)) % 60;
    const hours = Math.floor(duration / (1000 * 60 * 60));
    
    if (hours > 0) {
      return `${hours}h ${minutes}m ${seconds}s`;
    } else if (minutes > 0) {
      return `${minutes}m ${seconds}s`;
    } else {
      return `${seconds}s`;
    }
  }

  // Private methods
  private generateOperationId(): string {
    const timestamp = new Date().toISOString().replace(/[-:.]/g, '').slice(0, 15);
    const random = Math.random().toString(36).substring(2, 6);
    return `OP_${timestamp}_${random}`;
  }

  private getModelPricing(modelId: string): {
    inputCostPer1k: number;
    outputCostPer1k: number;
    cacheReadCostPer1k: number;
    cacheWriteCostPer1k: number;
  } {
    // All Ollama models are free (local execution)
    if (this.config.modelProvider === 'ollama') {
      return {
        inputCostPer1k: 0,
        outputCostPer1k: 0,
        cacheReadCostPer1k: 0,
        cacheWriteCostPer1k: 0
      };
    }

    // Try to get pricing from configuration overrides first
    if (this.config.modelPricing && this.config.modelPricing[modelId]) {
      const pricing = this.config.modelPricing[modelId];
      const inputCost = pricing.inputCostPer1k;
      return {
        inputCostPer1k: inputCost,
        outputCostPer1k: pricing.outputCostPer1k,
        // Cache pricing: read is ~25% of input, write is ~125% of input (if not specified)
        cacheReadCostPer1k: pricing.cacheReadCostPer1k ?? inputCost * 0.25,
        cacheWriteCostPer1k: pricing.cacheWriteCostPer1k ?? inputCost * 1.25
      };
    }

    // Next, try models.dev catalog (best-effort synchronous read of cached data)
    try {
      const cached = getPricingPer1kSync(modelId);
      if (cached) {
        return {
          inputCostPer1k: cached.input,
          outputCostPer1k: cached.output,
          cacheReadCostPer1k: cached.cache_read,
          cacheWriteCostPer1k: cached.cache_write,
        };
      }
      // Trigger async load for future calls, but don't block now
      void getPricingPer1k(modelId).then(() => {}).catch(() => {});
    } catch {
      // ignore
    }

    // Fallback to model info (derived from overrides) if still nothing
    const model = this.getModelInfo(modelId);
    if (model) {
      return {
        inputCostPer1k: model.inputCostPer1k,
        outputCostPer1k: model.outputCostPer1k,
        cacheReadCostPer1k: model.inputCostPer1k * 0.25,
        cacheWriteCostPer1k: model.inputCostPer1k * 1.25
      };
    }

    return {
      inputCostPer1k: 0,
      outputCostPer1k: 0,
      cacheReadCostPer1k: 0,
      cacheWriteCostPer1k: 0
    };
  }

  private loadSessionData(): void {
    // Load session data from memory (localStorage not available in Node.js)
    // In production, this would use a file-based storage or database
    try {
      // For now, just use in-memory storage
      // Session data initialized silently
    } catch (error) {
      // Only log errors to avoid interfering with React Ink UI
      loggingService.warn('Failed to load session data:', error);
    }
  }

  private saveSessionData(): void {
    // Save session data (localStorage not available in Node.js)
    // In production, this would use a file-based storage or database
    try {
      // For now, just use in-memory storage
      // Session data saved to memory - silent operation
    } catch (error) {
      loggingService.warn('Failed to save session data:', error);
    }
  }

  // Clean up and save data
  destroy(): void {
    this.saveSessionData();
  }
}
