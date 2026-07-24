/**
 * Application-wide type definitions
 * Replaces 'any' types with proper TypeScript interfaces
 */

import { Operation } from '../services/OperationManager.js';
import type { OperationHealthSnapshot } from '../utils/operationHealthFormatting.js';

/**
 * Application state actions interface
 */
export interface ApplicationActions {
  setActiveOperation: (operation: Operation | null) => void;
  updateOperation: (updates: Partial<Operation>) => void;
  setHasCompletedOperation: (completed: boolean) => void;
  setUserHandoffActive: (active: boolean) => void;
  setExecutionService: (service: any) => void;
  setCompletedOperation: (operation: Operation | null) => void;
  clearCompletedOperation: () => void;
  updateMetrics: (metrics: OperationMetrics | null) => void;
  updateHealth: (health: OperationHealthSnapshot | null) => void;
  resetErrorCount: () => void;
  setStaticKey: (key: number) => void;
}

/**
 * Operation metrics interface
 */
export interface OperationMetrics {
  tokens: number;
  cost: number;
  duration: string;
  memoryOps: number;
  evidence: number;
  progressPercent: number;
}

/**
 * Assessment flow state interface
 */
export interface AssessmentFlowState {
  currentStep: string;
  isActive: boolean;
  targetUrl: string | null;
  objective: string | null;
  module: string | null;
  hasConfirmedSafety: boolean;
  executionParams: Record<string, any> | null;
}

/**
 * Modal context interface
 */
export interface ModalContext {
  operation?: Operation;
  selectedDoc?: number;
  message?: string;
  [key: string]: any;
}

/**
 * Application configuration interface
 */
export interface ApplicationConfig {
  docker: {
    useDocker: boolean;
    containerName: string;
    volumes: string[];
  };
  execution: {
    timeout: number;
    maxIterations: number;
    requireConfirmation: boolean;
  };
  model: {
    provider: string;
    modelId: string;
    region?: string;
  };
  memory: {
    backend: string;
    path?: string;
  };
  observability?: {
    enabled: boolean;
    provider?: string;
  };
  [key: string]: any;
}

/**
 * Stream event interface
 */
export interface StreamEvent {
  type: string;
  content?: string;
  tool_name?: string;
  tool_input?: any;
  context?: string;
  startTime?: number;
  delay?: number;
  operation_stage?: string;
  evaluation_step_index?: number;
  evaluation_step_total?: number;
  evaluation_step_kind?: string;
  evaluation_scope?: string;
  evaluation_metric?: string;
  evaluation_step_label?: string;
  report_step_index?: number;
  report_step_total?: number;
  report_step_kind?: string;
  report_step_label?: string;
  status?: string;
  scores?: Record<string, number>;
  metrics_evaluated?: number;
  average_score?: number | null;
  [key: string]: any;
}
