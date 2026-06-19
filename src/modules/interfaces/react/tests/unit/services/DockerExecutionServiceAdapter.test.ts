import {beforeEach, describe, expect, it, jest} from '@jest/globals';
import {EventEmitter} from 'events';
import {ExecutionMode} from '../../../src/services/ExecutionService.js';
import type {Config} from '../../../src/contexts/ConfigContext.js';

const dockerInstances: MockDirectDockerService[] = [];
const execMock = jest.fn((command: string, callback: (error: Error | null, stdout?: string, stderr?: string) => void) => {
    if (command.startsWith('docker image inspect ')) {
        callback(new Error('image missing'), '', 'missing');
        return;
    }
    callback(new Error(`unexpected command: ${command}`), '', '');
});

class MockDirectDockerService extends EventEmitter {
    static checkDocker = jest.fn(async () => true);

    executeAssessment = jest.fn(async () => undefined);
    stop = jest.fn(async () => undefined);
    cleanup = jest.fn();
    isAssessing = jest.fn(() => false);

    constructor() {
        super();
        dockerInstances.push(this);
    }
}

class MockContainerManager extends EventEmitter {
    static instance = new MockContainerManager();
    static getInstance = jest.fn(() => MockContainerManager.instance);

    checkContainerStatus = jest.fn(async () => ({
        requiredContainers: {
            'full-stack': {
                missing: ['langfuse-web', 'postgres'],
                needsRestart: ['redis'],
            },
            'single-container': {
                missing: ['cyber-autoagent'],
                needsRestart: ['cyber-autoagent-old'],
            },
        },
    }));
    switchToMode = jest.fn(async (_mode: string) => undefined);
}

jest.unstable_mockModule('../../../src/services/DirectDockerService.js', () => ({
    DirectDockerService: MockDirectDockerService,
}));

jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({
    ContainerManager: MockContainerManager,
}));

jest.unstable_mockModule('node:child_process', () => ({
    exec: execMock,
}));

jest.unstable_mockModule('child_process', () => ({
    exec: execMock,
}));

const baseConfig = {
    modelProvider: 'ollama',
    ollamaHost: 'http://localhost:11434',
    dockerImage: 'cyber-autoagent:test',
    outputDir: '/private/tmp/cyber-autoagent-docker-adapter-test',
} as unknown as Config;

const loadAdapter = async (mode: ExecutionMode = ExecutionMode.DOCKER_SINGLE) => {
    const {DockerExecutionServiceAdapter} = await import('../../../src/services/DockerExecutionServiceAdapter.js');
    const adapter = new DockerExecutionServiceAdapter(mode);
    return {
        adapter,
        dockerService: dockerInstances.at(-1)!,
        containerManager: MockContainerManager.instance,
    };
};

describe('DockerExecutionServiceAdapter', () => {
    beforeEach(() => {
        dockerInstances.length = 0;
        MockDirectDockerService.checkDocker.mockResolvedValue(true);
        MockContainerManager.instance.removeAllListeners();
        MockContainerManager.instance.checkContainerStatus.mockClear();
        MockContainerManager.instance.switchToMode.mockClear();
        execMock.mockClear();
        process.env.NODE_ENV = 'test';
        delete process.env.DEV;
    });

    it('rejects non-Docker execution modes', async () => {
        const {DockerExecutionServiceAdapter} = await import('../../../src/services/DockerExecutionServiceAdapter.js');

        expect(() => new DockerExecutionServiceAdapter(ExecutionMode.PYTHON_CLI)).toThrow('Invalid Docker mode');
    });

    it('reports mode, capabilities, and support', async () => {
        const {adapter} = await loadAdapter(ExecutionMode.DOCKER_STACK);

        expect(adapter.getMode()).toBe(ExecutionMode.DOCKER_STACK);
        expect(adapter.getCapabilities()).toEqual(expect.objectContaining({
            canExecute: true,
            supportsStreaming: true,
            supportsParallel: true,
            maxConcurrent: 5,
        }));
        await expect(adapter.isSupported(baseConfig)).resolves.toBe(true);

        MockDirectDockerService.checkDocker.mockRejectedValueOnce(new Error('daemon down'));
        await expect(adapter.isSupported(baseConfig)).resolves.toBe(false);
    });

    it('returns an early validation error when Docker is unavailable', async () => {
        MockDirectDockerService.checkDocker.mockResolvedValueOnce(false);
        const {adapter} = await loadAdapter();

        await expect(adapter.validate(baseConfig)).resolves.toEqual(expect.objectContaining({
            valid: false,
            error: 'Docker Engine is not available',
            issues: [expect.objectContaining({type: 'docker', message: 'Docker Engine is not running'})],
        }));
    });

    it('validates single-container mode with setup warnings and missing image error', async () => {
        const {adapter} = await loadAdapter(ExecutionMode.DOCKER_SINGLE);

        const result = await adapter.validate({
            ...baseConfig,
            ollamaHost: undefined,
        } as Config);

        expect(result.valid).toBe(false);
        expect(result.warnings).toEqual(expect.arrayContaining([
            'Container will be created for single-container mode',
            'Container needs to be restarted',
            'Using default Ollama host (localhost:11434)',
        ]));
        expect(result.issues).toEqual(expect.arrayContaining([
            expect.objectContaining({type: 'docker', message: 'Cyber-AutoAgent Docker image not found'}),
        ]));
        expect(execMock).toHaveBeenCalledWith('docker image inspect cyber-autoagent:test', expect.any(Function));
    });

    it.skip('downgrades missing development images to warnings in stack mode', async () => {
        process.env.NODE_ENV = 'development';
        const {adapter} = await loadAdapter(ExecutionMode.DOCKER_STACK);

        const result = await adapter.validate(baseConfig);

        expect(result.valid).toBe(true);
        expect(result.warnings).toEqual(expect.arrayContaining([
            '2 containers need to be created for full-stack mode',
            '1 containers need to be restarted',
            'Docker image cyber-autoagent:test not found. Build with: docker build -t cyber-autoagent:test .',
        ]));
    });

    it('forwards service and container progress events', async () => {
        const {adapter, dockerService, containerManager} = await loadAdapter();
        const events: string[] = [];

        adapter.on('started', () => events.push('started'));
        adapter.on('event', event => events.push(event.type));
        adapter.on('progress', message => events.push(message));
        adapter.on('stopped', () => events.push('stopped'));
        adapter.on('complete', result => events.push(result.success ? 'complete' : 'failed'));

        dockerService.emit('started');
        dockerService.emit('event', {type: 'tool_result'});
        containerManager.emit('progress', 'pulling image');
        dockerService.emit('stopped');
        dockerService.emit('complete');

        expect(events).toEqual(['started', 'tool_result', 'pulling image', 'stopped', 'complete']);
    });

    it('creates execution handles that resolve on complete and block concurrent runs', async () => {
        const {adapter, dockerService, containerManager} = await loadAdapter(ExecutionMode.DOCKER_STACK);
        dockerService.isAssessing.mockReturnValue(true);

        const handle = await adapter.execute({module: 'web', target: 'example.com'} as any, baseConfig);

        expect(containerManager.switchToMode).toHaveBeenCalledWith('full-stack');
        expect(handle.id).toMatch(/^docker-docker-stack-/);
        expect(handle.isActive()).toBe(true);
        await expect(adapter.execute({} as any, baseConfig)).rejects.toThrow('Docker execution already active');

        dockerService.emit('complete');
        await expect(handle.result).resolves.toEqual(expect.objectContaining({success: true}));
    });

    it('resolves execution handles on stopped and error events', async () => {
        const stopped = await loadAdapter();
        const stoppedHandle = await stopped.adapter.execute({} as any, baseConfig);
        stopped.dockerService.emit('stopped');
        await expect(stoppedHandle.result).resolves.toEqual(expect.objectContaining({
            success: false,
            error: 'Assessment stopped before completion',
        }));

        const errored = await loadAdapter();
        errored.adapter.on('error', () => {
        });
        const erroredHandle = await errored.adapter.execute({} as any, baseConfig);
        errored.dockerService.emit('error', new Error('boom'));
        await expect(erroredHandle.result).resolves.toEqual(expect.objectContaining({
            success: false,
            error: 'boom',
        }));
    });

    it('delegates setup, stop events, and cleanup', async () => {
        const {adapter, dockerService, containerManager} = await loadAdapter();
        const progress: string[] = [];

        await adapter.setup(baseConfig, message => progress.push(message));
        containerManager.emit('progress', 'after setup');
        adapter.emit('stop');
        adapter.cleanup();

        expect(containerManager.switchToMode).toHaveBeenCalledWith('single-container');
        expect(progress).toEqual([]);
        expect(dockerService.stop).toHaveBeenCalled();
        expect(dockerService.cleanup).toHaveBeenCalled();
        expect(containerManager.listenerCount('progress')).toBe(0);
        expect(adapter.listenerCount('stop')).toBe(0);
    });
});
