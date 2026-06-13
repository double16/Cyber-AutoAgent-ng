/**
 * Verify DirectDockerService sets pricing override env vars from config.modelPricing
 */
import { describe, it, expect, jest } from '@jest/globals';
import { DirectDockerService } from '../../../src/services/DirectDockerService.js';
import { ContainerManager } from '../../../src/services/ContainerManager.js';

describe('DirectDockerService pricing env override', () => {
  it('adds CYBER_AGENT_PRICING_* to Env when modelPricing has entry for modelId', async () => {
    // Mock deployment mode to avoid exec reuse complexities
    (ContainerManager as any).getInstance = jest.fn(() => ({
      getCurrentMode: jest.fn(async () => 'local-cli')
    }));

    const svc = new DirectDockerService();
    // Ensure reuse path is skipped explicitly
    (svc as any).findServiceContainer = jest.fn(async () => null);

    // Replace internal docker client with a stub that captures createContainer options
    let capturedEnv: string[] | undefined;
    (svc as any).dockerClient = {
      listContainers: async () => [],
      createContainer: async (opts: any) => {
        capturedEnv = opts?.Env as string[];
        throw new Error('abort-after-capture');
      }
    };

    const cfg: any = {
      iterations: 1,
      modelProvider: 'openai',
      modelId: 'gpt-4o',
      outputDir: './outputs-test',
      mcp: { enabled: false, connections: [] },
      modelPricing: {
        'gpt-4o': { inputCostPer1k: 5, outputCostPer1k: 15, cacheReadCostPer1k: 0.5, cacheWriteCostPer1k: 2 }
      }
    };
    const params: any = { module: 'web', target: 'example.com', objective: 'test' };

    try {
      await svc.executeAssessment(params, cfg);
    } catch (e: any) {
      // swallow; we're only interested in captured Env
      // eslint-disable-next-line no-console
      console.log('DirectDockerService test caught error:', e?.message);
    }

    expect(Array.isArray(capturedEnv)).toBe(true);
    const has = (k: string, v: string) => capturedEnv!.some(e => e === `${k}=${v}`);
    expect(has('CYBER_AGENT_PRICING_INPUT', '0.005')).toBe(true);
    expect(has('CYBER_AGENT_PRICING_OUTPUT', '0.015')).toBe(true);
    expect(has('CYBER_AGENT_PRICING_CACHE_READ', '0.0005')).toBe(true);
    expect(has('CYBER_AGENT_PRICING_CACHE_WRITE', '0.002')).toBe(true);
  });
});
