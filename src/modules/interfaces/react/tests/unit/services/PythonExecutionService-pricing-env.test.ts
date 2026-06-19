/**
 * Verify PythonExecutionService sets pricing override env vars from config.modelPricing
 */
import { describe, it, expect, jest } from '@jest/globals';

// Mock fs.existsSync to always return true for venv python presence
jest.unstable_mockModule('fs', () => ({
  default: jest.requireActual('fs'),
  ...jest.requireActual('fs'),
  existsSync: jest.fn(() => true)
}));

// Mock child_process via ESM-unfriendly API: use unstable_mockModule before dynamic import
jest.unstable_mockModule('child_process', async () => {
  const ee = await import('events');
  const actual = jest.requireActual('child_process');
  return {
    __esModule: true,
    ...actual,
    spawn: jest.fn((_cmd: string, _args: string[], opts: any) => {
      const proc: any = new (ee as any).EventEmitter();
      proc.stdout = new (ee as any).EventEmitter();
      proc.stderr = new (ee as any).EventEmitter();
      (proc as any).__opts = opts;
      setTimeout(() => proc.emit('exit', 0), 10);
      return proc;
    })
  } as any;
});

describe('PythonExecutionService pricing env override', () => {
  it('sets CYBER_AGENT_PRICING_* when modelPricing has entry for modelId', async () => {
    const mod = await import('../../../src/services/PythonExecutionService.js');
    const PythonExecutionService = (mod as any).PythonExecutionService as any;
    const svc = new PythonExecutionService();
    // Avoid background logging from preflight checks after test completes
    (svc as any).preflightChecks = jest.fn(async () => true);
    const cfg: any = {
      iterations: 1,
      modelProvider: 'openai',
      modelId: 'gpt-4o',
      modelPricing: {
        'gpt-4o': { inputCostPer1k: 5, outputCostPer1k: 15, cacheReadCostPer1k: 0.5, cacheWriteCostPer1k: 2 }
      }
    };
    const params: any = { module: 'web', target: 'example.com', objective: 'test' };

    await svc.executeAssessment(params, cfg);

    const spawn = (await import('child_process')).spawn as unknown as jest.Mock;
    expect(spawn).toHaveBeenCalled();
    const lastCall = spawn.mock.calls[spawn.mock.calls.length - 1];
    const opts = (spawn.mock.results[spawn.mock.results.length - 1].value as any).__opts || lastCall?.[2];
    expect(opts).toBeTruthy();
    const env = opts.env as Record<string, string>;
    expect(env.CYBER_AGENT_PRICING_INPUT).toBe('0.005');
    expect(env.CYBER_AGENT_PRICING_OUTPUT).toBe('0.015');
    expect(env.CYBER_AGENT_PRICING_CACHE_READ).toBe('0.0005');
    expect(env.CYBER_AGENT_PRICING_CACHE_WRITE).toBe('0.002');
  });
});
