import {jest} from '@jest/globals';
import {DEFAULT_EXECUTION_CONFIG, ExecutionMode} from '../../../src/services/ExecutionService.js';
import {ExecutionServiceFactory} from '../../../src/services/ExecutionServiceFactory.js';
import {TestExecutionService} from '../../../src/services/TestExecutionService.js';

describe('ExecutionServiceFactory selection in mock mode', () => {
    const originalEnv = {...process.env};
    let consoleSpies: Array<jest.SpiedFunction<any>> = [];

    beforeEach(() => {
        process.env = {
            ...originalEnv,
            CYBER_TEST_MODE: 'true',
            CYBER_TEST_EXECUTION: 'mock',
        };
        consoleSpies = [
            jest.spyOn(console, 'info').mockImplementation(() => {
            }),
            jest.spyOn(console, 'warn').mockImplementation(() => {
            }),
            jest.spyOn(console, 'error').mockImplementation(() => {
            }),
        ];
        ExecutionServiceFactory.cleanup();
    });

    afterEach(() => {
        ExecutionServiceFactory.cleanup();
        for (const spy of consoleSpies) spy.mockRestore();
        consoleSpies = [];
        process.env = {...originalEnv};
    });

    it('lists registered execution modes', async () => {
        await expect(ExecutionServiceFactory.getAvailableModes()).resolves.toEqual([
            ExecutionMode.PYTHON_CLI,
            ExecutionMode.DOCKER_SINGLE,
            ExecutionMode.DOCKER_STACK,
        ]);

        await expect(ExecutionServiceFactory.isModeAvailable(ExecutionMode.PYTHON_CLI)).resolves.toBe(true);
    });

    it('creates the mock Python execution service in test mode', async () => {
        const service = await ExecutionServiceFactory.createService(ExecutionMode.PYTHON_CLI);

        expect(service).toBeInstanceOf(TestExecutionService);
        expect(service.getMode()).toBe(ExecutionMode.PYTHON_CLI);
        service.cleanup();
    });

    it('selects preferred mock Python service and returns validation details', async () => {
        const result = await ExecutionServiceFactory.selectService({} as any, {
            ...DEFAULT_EXECUTION_CONFIG,
            preferredMode: ExecutionMode.PYTHON_CLI,
            fallbackModes: [],
            validationTimeoutMs: 100,
        });

        expect(result.mode).toBe(ExecutionMode.PYTHON_CLI);
        expect(result.isPreferred).toBe(true);
        expect(result.service).toBeInstanceOf(TestExecutionService);
        expect(result.validation).toEqual({valid: true, issues: [], warnings: []});
        expect(result.rejected).toEqual([]);

        result.service.cleanup();
    });

    it('reports Python mode capabilities without leaking the service', async () => {
        await expect(ExecutionServiceFactory.getModeCapabilities(ExecutionMode.PYTHON_CLI)).resolves.toEqual([
            'execution',
            'streaming',
        ]);
    });
});
