import {jest} from '@jest/globals';
import {EventEmitter} from 'events';
import {ExecutionMode} from '../../../src/services/ExecutionService.js';
import type {Config} from '../../../src/contexts/ConfigContext.js';

const serviceInstances: MockPythonService[] = [];

class MockPythonService extends EventEmitter {
    checkPythonVersion = jest.fn(async () => ({installed: true}));
    checkEnvironmentStatus = jest.fn(async () => ({
        venvExists: false,
        venvValid: false,
        dependenciesInstalled: false,
        packageInstalled: false,
    }));
    executeAssessment = jest.fn(async () => undefined);
    setupPythonEnvironment = jest.fn(async (_onProgress?: (message: string) => void) => {
        _onProgress?.('setup');
    });
    sendUserInput = jest.fn(async (_input: string) => undefined);
    stop = jest.fn(async () => undefined);
    cleanup = jest.fn();
    isActive = jest.fn(() => false);
    projectRoot = process.cwd();

    constructor() {
        super();
        serviceInstances.push(this);
    }
}

jest.unstable_mockModule('../../../src/services/PythonExecutionService.js', () => ({
    PythonExecutionService: MockPythonService,
}));

const baseConfig = {
    modelProvider: 'ollama',
    ollamaHost: 'http://localhost:11434',
    outputDir: '/private/tmp/cyber-autoagent-python-adapter-test',
} as unknown as Config;

const loadAdapter = async () => {
    const {PythonExecutionServiceAdapter} = await import('../../../src/services/PythonExecutionServiceAdapter.js');
    const adapter = new PythonExecutionServiceAdapter();
    return {adapter, service: serviceInstances.at(-1)!};
};

describe('PythonExecutionServiceAdapter', () => {
    beforeEach(() => {
        serviceInstances.length = 0;
        delete process.env.CONTAINER;
        delete process.env.IS_DOCKER;
    });

    it('reports mode and capabilities', async () => {
        const {adapter} = await loadAdapter();

        expect(adapter.getMode()).toBe(ExecutionMode.PYTHON_CLI);
        expect(adapter.getCapabilities()).toEqual(expect.objectContaining({
            canExecute: true,
            supportsStreaming: true,
            supportsParallel: false,
            maxConcurrent: 1,
        }));
    });

    it('checks Python support and handles check failures', async () => {
        const {adapter, service} = await loadAdapter();

        await expect(adapter.isSupported(baseConfig)).resolves.toBe(true);

        service.checkPythonVersion.mockRejectedValueOnce(new Error('missing'));
        await expect(adapter.isSupported(baseConfig)).resolves.toBe(false);
    });

    it('validates host Python environment and credential warnings', async () => {
        const {adapter, service} = await loadAdapter();
        service.checkPythonVersion.mockResolvedValueOnce({installed: false, error: 'no python'});

        const result = await adapter.validate({
            ...baseConfig,
            modelProvider: 'bedrock',
            awsAccessKeyId: undefined,
            awsBearerToken: undefined,
        } as Config);

        expect(result.valid).toBe(false);
        expect(result.error).toBe('Python execution environment validation failed');
        expect(result.issues).toEqual(expect.arrayContaining([
            expect.objectContaining({type: 'python', message: 'no python'}),
            expect.objectContaining({type: 'credentials'}),
        ]));
        expect(result.warnings).toEqual(expect.arrayContaining([
            'Virtual environment will be created during setup',
            'Python dependencies will be installed during setup',
            'Cyber-AutoAgent package will be installed during setup',
        ]));
    });

    it('uses Docker-specific validation shortcuts inside containers', async () => {
        process.env.CONTAINER = 'docker';
        const {adapter, service} = await loadAdapter();
        service.checkPythonVersion.mockResolvedValueOnce({installed: false});

        const result = await adapter.validate({
            ...baseConfig,
            ollamaHost: undefined,
        } as Config);

        expect(result.valid).toBe(false);
        expect(service.checkEnvironmentStatus).not.toHaveBeenCalled();
        expect(result.issues).toEqual(expect.arrayContaining([
            expect.objectContaining({type: 'python', message: 'Python not found in Docker container'}),
        ]));
        expect(result.warnings).toContain('Using default Ollama host (localhost:11434)');
    });

    it('forwards events from the wrapped service', async () => {
        const {adapter, service} = await loadAdapter();
        const events: string[] = [];

        adapter.on('started', () => events.push('started'));
        adapter.on('event', event => events.push(event.type));
        adapter.on('progress', message => events.push(message));
        adapter.on('stopped', () => events.push('stopped'));
        adapter.on('complete', result => events.push(result.success ? 'complete' : 'failed'));

        service.emit('started');
        service.emit('event', {type: 'tool_start'});
        service.emit('progress', 'working');
        service.emit('stopped');
        service.emit('complete');

        expect(events).toEqual(['started', 'tool_start', 'working', 'stopped', 'complete']);
    });

    it('creates execution handles and prevents concurrent active runs', async () => {
        const {adapter, service} = await loadAdapter();
        let resolveExecution!: () => void;
        service.executeAssessment.mockReturnValueOnce(new Promise<void>(resolve => {
            resolveExecution = resolve;
        }));
        service.isActive.mockReturnValue(true);

        const handle = await adapter.execute({module: 'web', target: 'example.com'} as any, baseConfig);

        expect(handle.id).toMatch(/^python-/);
        expect(handle.isActive()).toBe(true);
        await expect(adapter.execute({} as any, baseConfig)).rejects.toThrow('Python execution already active');

        await handle.stop();
        expect(service.stop).toHaveBeenCalled();

        resolveExecution();
        await expect(handle.result).resolves.toEqual(expect.objectContaining({success: true}));
    });

    it('delegates setup, user input, stop events, and cleanup', async () => {
        const {adapter, service} = await loadAdapter();
        const progress: string[] = [];

        await adapter.setup(baseConfig, message => progress.push(message));
        await adapter.sendUserInput('y');
        adapter.emit('stop');
        adapter.cleanup();

        expect(progress).toEqual(['setup']);
        expect(service.setupPythonEnvironment).toHaveBeenCalled();
        expect(service.sendUserInput).toHaveBeenCalledWith('y');
        expect(service.stop).toHaveBeenCalled();
        expect(service.cleanup).toHaveBeenCalled();
        expect(adapter.listenerCount('stop')).toBe(0);
    });
});
