/**
 * Tests for OperationManager integration with ModelsCatalog/pricing
 */
import { describe, it, beforeEach, expect, jest } from '@jest/globals';
import type { Config } from '../../../src/contexts/ConfigContext.js';

// Mock the ModelsCatalog module (ESM-safe via jest.mock + requireMock for configuration)
jest.mock('../../../src/services/ModelsCatalog.js', () => {
  const peekAllModels = jest.fn();
  const loadAllModels = jest.fn();
  const getAllModels = jest.fn();
  const findModel = jest.fn();
  const peekCatalog = jest.fn();
  const getPricingPer1kSync = jest.fn();
  const getPricingPer1k = jest.fn();
  const getContextLimitSync = jest.fn();
  const getContextLimit = jest.fn();
  return {
    __esModule: true,
    modelsCatalog: { peekAllModels, getAllModels, findModel, peekCatalog },
    peekAllModels,
    loadAllModels,
    getPricingPer1kSync,
    getPricingPer1k,
    getContextLimitSync,
    getContextLimit,
  };
});

import { OperationManager } from '../../../src/services/OperationManager.js';
// Get the live mock object to set return values
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const CatalogMock: any = jest.requireMock('../../../src/services/ModelsCatalog.js');

describe('OperationManager + ModelsCatalog/pricing', () => {
  let baseConfig: Config;

  beforeEach(() => {
    jest.clearAllMocks();

    baseConfig = {
      modelProvider: 'bedrock',
      modelId: 'us.anthropic.claude-sonnet-4-20250514-v1:0',
      awsRegion: 'us-east-1',
      dockerImage: 'image',
      dockerTimeout: 300,
      volumes: [],
      iterations: 10,
      autoApprove: true,
      confirmations: false,
      maxThreads: 5,
      outputFormat: 'markdown',
      verbose: false,
      memoryMode: 'auto',
      keepMemory: true,
      memoryBackend: 'FAISS',
      outputDir: './outputs',
      unifiedOutput: true,
      theme: 'retro',
      showMemoryUsage: false,
      showOperationId: true,
      environment: {},
      reportSettings: { includeRemediation: true, includeCWE: true, includeTimestamps: true, includeEvidence: true, includeMemoryOps: true },
      observability: false,
      isConfigured: true,
      allowExecutionFallback: true,
      modelPricing: {},
    } as any;
  });

  it('lists pricing-derived models and infers provider and costs when catalog is unavailable', () => {
    // No catalog available
    CatalogMock.peekAllModels.mockReturnValue(null);

    // Provide pricing for an OpenAI model; provider should be inferred as 'litellm'
    const cfg = {
      ...baseConfig,
      modelPricing: {
        'openai/gpt-4.1-mini': { inputCostPer1k: 3, outputCostPer1k: 15 },
      },
    } as Config;

    const om = new OperationManager(cfg);
    const models = om.getAvailableModels();

    const m = models.find(x => x.id === 'openai/gpt-4.1-mini');
    expect(m).toBeTruthy();
    expect(m!.name).toBe('openai/gpt-4.1-mini');
    expect(m!.provider).toBe('litellm');
    expect(m!.contextLimit).toBe(8000);
    expect(m!.inputCostPer1k).toBe(3);
    expect(m!.outputCostPer1k).toBe(15);
  });

  it('falls back to raw id for display name when catalog is empty and uses pricing-derived list', () => {
    CatalogMock.peekAllModels.mockReturnValue(null);

    const cfg = {
      ...baseConfig,
      modelPricing: {
        'bedrock/vendor.custom-model-v1': { inputCostPer1k: 0.001, outputCostPer1k: 0.002 },
      },
    } as Config;

    const om = new OperationManager(cfg);
    const models = om.getAvailableModels();
    const m = models.find(x => x.id === 'bedrock/vendor.custom-model-v1');
    expect(m).toBeTruthy();
    expect(m!.name).toBe('bedrock/vendor.custom-model-v1');
  });

  it('uses pricing override when provided in config', () => {
    const cfg = {
      ...baseConfig,
      modelPricing: {
        'provider/special-model': { inputCostPer1k: 1.0, outputCostPer1k: 2.0 },
      },
    } as Config;

    const om = new OperationManager(cfg);
    const op = om.startOperation('mod', 'tgt', 'obj', 'provider/special-model');
    expect(op.cost.modelPricing.inputCostPer1k).toBeCloseTo(1.0, 6);
    expect(op.cost.modelPricing.outputCostPer1k).toBeCloseTo(2.0, 6);
    expect(op.cost.modelPricing.cacheReadCostPer1k).toBeCloseTo(0.25, 6);
    expect(op.cost.modelPricing.cacheWriteCostPer1k).toBeCloseTo(1.25, 6);
  });

  it('returns zero costs when provider is ollama (local)', () => {
    const cfg = { ...baseConfig, modelProvider: 'ollama' } as Config;
    const om = new OperationManager(cfg);
    const op = om.startOperation('mod', 'tgt', 'obj', 'llama3.1:8b');
    expect(op.cost.modelPricing).toEqual({ inputCostPer1k: 0, outputCostPer1k: 0, cacheReadCostPer1k: 0, cacheWriteCostPer1k: 0 });
  });

  it('defaults context limit to 8000 when not available in any source', () => {
    const cfg = {
      ...baseConfig,
      modelPricing: {
        'prov/a': { inputCostPer1k: 0.001, outputCostPer1k: 0.002 },
        'prov/b': { inputCostPer1k: 0.001, outputCostPer1k: 0.002 },
      },
    } as Config;

    CatalogMock.peekAllModels.mockReturnValue(null);

    const om = new OperationManager(cfg);
    const models = om.getAvailableModels();
    const a = models.find(x => x.id === 'prov/a')!;
    const b = models.find(x => x.id === 'prov/b')!;
    expect(a.contextLimit).toBe(8000);
    expect(b.contextLimit).toBe(8000);
  });
});
