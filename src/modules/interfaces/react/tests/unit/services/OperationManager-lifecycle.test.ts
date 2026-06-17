import {jest} from '@jest/globals';
import {OperationManager} from '../../../src/services/OperationManager.js';
import type {Config} from '../../../src/contexts/ConfigContext.js';

const config = {
    modelProvider: 'bedrock',
    modelPricing: {
        'model-a': {inputCostPer1k: 0.01, outputCostPer1k: 0.02},
        'llama3.2:3b': {inputCostPer1k: 1, outputCostPer1k: 1},
        'bedrock/vendor/model-b': {inputCostPer1k: 0.03, outputCostPer1k: 0.04},
    },
} as unknown as Config;

describe('OperationManager lifecycle', () => {
    it('starts operations with logs and resets cumulative token state', () => {
        const manager = new OperationManager(config);
        const op = manager.startOperation('web', 'example.com', 'objective', 'model-a', true, 'OP_OLD');

        expect(op).toEqual(expect.objectContaining({
            module: 'web',
            target: 'example.com',
            objective: 'objective',
            status: 'running',
            currentStep: 0,
            totalSteps: 50,
            continueOperation: true,
            reportOnly: 'OP_OLD',
        }));
        expect(op.id).toMatch(/^OP_/);
        expect(manager.getCurrentOperation()).toBe(op);
        expect(op.logs[0]).toEqual(expect.objectContaining({
            level: 'info',
            message: 'Operation started: web → example.com',
            step: 0,
        }));
    });

    it('updates progress, findings, model, and cumulative token usage', () => {
        const manager = new OperationManager(config);
        const op = manager.startOperation('web', 'example.com', 'objective', 'model-a');

        manager.updateProgress(op.id, 2, 10, 'Testing auth');
        manager.addFinding(op.id, 'SQL injection');
        expect(manager.switchModel(op.id, 'model-b')).toBe(true);
        manager.updateTokenUsage(op.id, 100, 50, 0.01, 10, 5);
        manager.updateTokenUsage(op.id, 90, 60, 0.02, 9, 8);

        const updated = manager.getOperation(op.id)!;
        expect(updated).toEqual(expect.objectContaining({
            currentStep: 2,
            totalSteps: 10,
            description: 'Testing auth',
            findings: 1,
            model: 'model-b',
        }));
        expect(updated.cost).toEqual({
            inputTokens: 100,
            outputTokens: 60,
            cacheReadTokens: 10,
            cacheWriteTokens: 8,
            tokensUsed: 160,
            estimatedCost: 0.02,
        });
        expect(updated.logs.map(log => log.message)).toEqual(expect.arrayContaining([
            'Step 2/10: Testing auth',
            'Finding #1: SQL injection',
            'Model switched from model-a to model-b',
        ]));
    });

    it('pauses, resumes, and completes operations', () => {
        const manager = new OperationManager(config);
        const op = manager.startOperation('web', 'example.com', 'objective', 'model-a');

        expect(manager.pauseOperation(op.id)).toBe(true);
        expect(manager.pauseOperation(op.id)).toBe(false);
        expect(manager.resumeOperation(op.id)).toBe(true);
        expect(manager.resumeOperation(op.id)).toBe(false);

        manager.completeOperation(op.id, true);

        const completed = manager.getOperation(op.id)!;
        expect(completed.status).toBe('completed');
        expect(completed.endTime).toBeInstanceOf(Date);
        expect(manager.getCurrentOperation()).toBeNull();
        expect(completed.logs.at(-1)?.level).toBe('success');
    });

    it('marks failed completions as error and formats durations', () => {
        const manager = new OperationManager(config);
        const op = manager.startOperation('web', 'example.com', 'objective', 'model-a');
        op.startTime = new Date(Date.now() - 3_661_000);

        expect(manager.getOperationDuration(op.id)).toMatch(/^1h 1m \d+s$/);
        manager.completeOperation(op.id, false);

        expect(manager.getOperation(op.id)?.status).toBe('error');
        expect(manager.getOperation(op.id)?.logs.at(-1)?.level).toBe('error');
        expect(manager.getOperationDuration('missing')).toBe('0s');
    });

    it('renames operation ids and merges when target id already exists', () => {
        const manager = new OperationManager(config);
        const first = manager.startOperation('web', 'a.example', 'first', 'model-a');
        first.findings = 2;
        first.logs.push({timestamp: new Date(), level: 'success', message: 'extra'});

        const oldFirstId = first.id;
        const renamed = manager.renameOperationId(oldFirstId, 'OP_BACKEND')!;
        expect(renamed.id).toBe('OP_BACKEND');
        expect(manager.getOperation(oldFirstId)).toBeNull();
        expect(manager.getOperation('OP_BACKEND')).toBe(renamed);
        expect(manager.getCurrentOperation()).toBe(renamed);

        const second = manager.startOperation('web', 'b.example', 'second', 'model-a');
        second.findings = 1;
        const merged = manager.renameOperationId(second.id, 'OP_BACKEND')!;

        expect(merged.id).toBe('OP_BACKEND');
        expect(merged.findings).toBe(2);
        expect(manager.getOperation(second.id)).toBeNull();
        expect(manager.renameOperationId('', 'x')).toBeNull();
        expect(manager.renameOperationId('missing', 'OP_BACKEND')).toBe(merged);
    });

    it('returns configured model info and ollama zero-cost overrides', () => {
        const manager = new OperationManager(config);
        const models = manager.getAvailableModels();

        expect(models.map(model => model.id)).toEqual(expect.arrayContaining(['model-a', 'llama3.2:3b']));
        expect(manager.getModelInfo('model-a')).toEqual(expect.objectContaining({
            id: 'model-a',
            provider: 'bedrock',
            inputCostPer1k: 0.01,
            outputCostPer1k: 0.02,
            contextLimit: 8000,
        }));
        expect(manager.getModelInfo('missing')).toBeNull();

        const ollamaManager = new OperationManager({...config, modelProvider: 'ollama'} as Config);
        expect(ollamaManager.getModelInfo('llama3.2:3b')).toEqual(expect.objectContaining({
            provider: 'ollama',
            inputCostPer1k: 0,
            outputCostPer1k: 0,
        }));
    });

    it('ignores lifecycle operations for missing ids and calls destroy without throwing', () => {
        const manager = new OperationManager(config);
        const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {
        });

        try {
            expect(manager.pauseOperation('missing')).toBe(false);
            expect(manager.resumeOperation('missing')).toBe(false);
            expect(manager.switchModel('missing', 'model-b')).toBe(false);
            manager.updateProgress('missing', 1, 1, 'noop');
            manager.updateOperation('missing', {status: 'cancelled'});
            manager.addFinding('missing', 'noop');
            manager.updateTokenUsage('missing', 1, 1, 1);
            manager.addLog('missing', 'info', 'noop');
            manager.completeOperation('missing');
            expect(() => manager.destroy()).not.toThrow();
        } finally {
            warnSpy.mockRestore();
        }
    });
});
