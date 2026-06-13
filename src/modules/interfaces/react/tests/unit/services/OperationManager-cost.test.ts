/**
 * OperationManager cost calculation tests across pricing matrices
 */
import { describe, it, expect, beforeEach, jest } from '@jest/globals';
import { OperationManager } from '../../../src/services/OperationManager.js';
import type { Config } from '../../../src/contexts/ConfigContext.js';

describe('OperationManager cost calculation', () => {
  const baseConfig: Config = {
    // minimal viable config
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
    modelPricing: {
      'us.anthropic.claude-sonnet-4-20250514-v1:0': { inputCostPer1k: 0.006, outputCostPer1k: 0.03 },
      'claude-3-haiku-20240307-v1:0': { inputCostPer1k: 0.00025, outputCostPer1k: 0.00125 },
      'llama3.2:3b': { inputCostPer1k: 0, outputCostPer1k: 0 }, // local free
    },
  } as any;

  it('accepts estimatedCost from cost metric', () => {
    const om = new OperationManager(baseConfig);
    const op = om.startOperation('web', 'example.com', 'assessment', 'us.anthropic.claude-sonnet-4-20250514-v1:0');

    om.updateTokenUsage(op.id, 2000, 1000, 0.042); // 2k input, 1k output
    const updated = om.getOperation(op.id)!;
    expect(Number(updated.cost.estimatedCost.toFixed(6))).toBeCloseTo(0.042, 6);
  });
});

// Additional tests for ModelsCatalog integration and fallbacks
describe('OperationManager + ModelsCatalog integration', () => {

  let cfg: Config;
  beforeEach(() => {
    jest.resetModules();
    jest.clearAllMocks();
    cfg = {
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

  it('falls back to raw id for display name when catalog is not loaded', () => {
    const localCfg = { ...cfg, modelPricing: { 'bedrock/vendor.custom-model-v1': { inputCostPer1k: 0.001, outputCostPer1k: 0.002 } } } as Config;
    const om = new OperationManager(localCfg);
    const models = om.getAvailableModels();
    const m = models.find(x => x.id === 'bedrock/vendor.custom-model-v1')!;
    expect(m.name).toBe('bedrock/vendor.custom-model-v1');
  });

  it('uses override pricing from config when present', () => {
    const localCfg = { ...cfg, modelPricing: { 'x/vendor.model': { inputCostPer1k: 0.01, outputCostPer1k: 0.02 } } } as Config;
    const om = new OperationManager(localCfg);
    const info = om.getModelInfo('x/vendor.model');
    expect(info).toBeTruthy();
    expect(info!.inputCostPer1k).toBeCloseTo(0.01, 6);
    expect(info!.outputCostPer1k).toBeCloseTo(0.02, 6);
  });

  it('returns zero costs for ollama provider', () => {
    const om = new OperationManager({ ...(cfg as any), modelProvider: 'ollama', modelPricing: { 'llama3.1:8b': { inputCostPer1k: 1, outputCostPer1k: 1 } } });
    const info = om.getModelInfo('llama3.1:8b');
    expect(info).toBeTruthy();
    expect(info!.inputCostPer1k).toBe(0);
    expect(info!.outputCostPer1k).toBe(0);
  });

  it('defaults context limit to 8000 when catalog data is unavailable', () => {
    const localCfg = { ...cfg, modelPricing: { 'prov/unknown': { inputCostPer1k: 0.001, outputCostPer1k: 0.002 } } } as Config;
    const om = new OperationManager(localCfg);
    const models = om.getAvailableModels();
    const m = models.find(x => x.id === 'prov/unknown')!;
    expect(m.contextLimit).toBe(8000);
  });
});
