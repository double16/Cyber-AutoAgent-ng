import {jest} from '@jest/globals';
import fs from 'fs';
import os from 'os';
import path from 'path';
import {TestExecutionService} from '../../../src/services/TestExecutionService.js';
import {ExecutionMode} from '../../../src/services/ExecutionService.js';

describe('TestExecutionService', () => {
    const originalEnv = {...process.env};

    beforeEach(() => {
        jest.useFakeTimers();
        process.env = {...originalEnv};
        delete process.env.CYBER_TEST_EVENTS_PATH;
    });

    afterEach(() => {
        jest.useRealTimers();
        process.env = {...originalEnv};
    });

    it('reports static mode, capabilities, and validation support', async () => {
        const service = new TestExecutionService();

        expect(service.getMode()).toBe(ExecutionMode.PYTHON_CLI);
        expect(service.getCapabilities()).toEqual({
            canExecute: true,
            supportsStreaming: true,
            supportsParallel: false,
            maxConcurrent: 1,
            requirements: ['Test mode only'],
        });
        await expect(service.isSupported()).resolves.toBe(true);
        await expect(service.validate()).resolves.toEqual({valid: true, issues: [], warnings: []});
    });

    it('streams default events and completes', async () => {
        const service = new TestExecutionService();
        const events: any[] = [];
        const started = jest.fn();
        const complete = jest.fn();
        service.on('event', event => events.push(event));
        service.on('started', started);
        service.on('complete', complete);

        const handle = await service.execute({}, {});
        expect(handle.isActive()).toBe(true);
        expect(service.isActive()).toBe(true);

        jest.advanceTimersByTime(10);
        expect(started).toHaveBeenCalledWith({id: 'test'});

        jest.advanceTimersByTime(60 * 13);
        await Promise.resolve();

        expect(events.map(event => event.type)).toEqual([
            'step_header',
            'reasoning',
            'tool_start',
            'output',
            'metrics_update',
            'tool_invocation_end',
            'step_header',
            'tool_start',
            'output',
            'tool_invocation_end',
            'step_header',
            'output',
        ]);
        expect(complete).toHaveBeenCalledWith(expect.objectContaining({success: true}));
        await expect(handle.result).resolves.toEqual(expect.objectContaining({success: true}));
        expect(handle.isActive()).toBe(false);
    });

    it('loads newline-delimited events from CYBER_TEST_EVENTS_PATH', async () => {
        const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'test-events-'));
        const file = path.join(dir, 'events.ndjson');
        fs.writeFileSync(file, [
            JSON.stringify({type: 'output', content: 'one'}),
            JSON.stringify({type: 'metrics_update', metrics: {tokens: 1}}),
            '',
        ].join('\n'));
        process.env.CYBER_TEST_EVENTS_PATH = file;

        const service = new TestExecutionService();
        const events: any[] = [];
        service.on('event', event => events.push(event));

        await service.execute({}, {});
        jest.advanceTimersByTime(60 * 3);

        expect(events).toEqual([
            {type: 'output', content: 'one'},
            {type: 'metrics_update', metrics: {tokens: 1}},
        ]);
    });

    it('stops and cleans up active execution', async () => {
        const service = new TestExecutionService();
        const stopped = jest.fn();
        service.on('stopped', stopped);

        const handle = await service.execute({}, {});
        await handle.stop();

        expect(stopped).toHaveBeenCalledTimes(1);
        expect(handle.isActive()).toBe(false);
        expect(service.isActive()).toBe(false);

        service.cleanup();
        expect(service.listenerCount('stopped')).toBe(0);
    });
});
