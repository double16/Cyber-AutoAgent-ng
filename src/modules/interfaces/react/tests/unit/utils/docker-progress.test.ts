import {jest} from '@jest/globals';
import {DockerProgressAggregator, DockerProgressUpdateMeta} from '../../../src/utils/docker-progress.js';

type Update = { message: string; meta?: DockerProgressUpdateMeta };

describe('DockerProgressAggregator', () => {
    it('summarizes pull, create, and start progress while suppressing noisy lines', () => {
        const updates: Update[] = [];
        const aggregator = new DockerProgressAggregator(
            ['api', 'db'],
            (message, meta) => updates.push({message, meta}),
            0
        );

        aggregator.setLabel('Pulling stack images…');
        aggregator.update([
            'Pulling api',
            'Downloading [====>]',
            'Status: Downloaded newer image for api',
            'Creating db ... done',
            'Starting api ... done',
            '',
        ].join('\n'));

        expect(updates.map(update => update.message)).toEqual([
            'Pulling stack images… 0/2 ready',
            'Pulling stack images… 1/2 ready',
            'Creating containers… 1/2',
            'Starting containers… 1/2',
        ]);
        expect(updates[1].meta).toEqual(expect.objectContaining({
            phase: 'pull',
            pullReady: 1,
            total: 2,
            ratio: 0.5,
        }));
        expect(updates[2].meta).toEqual(expect.objectContaining({
            phase: 'create',
            created: 1,
            ratio: 0.5,
        }));
        expect(updates[3].meta).toEqual(expect.objectContaining({
            phase: 'start',
            started: 1,
            ratio: 0.5,
        }));
    });

    it('tracks BuildKit step and export progress', () => {
        const updates: Update[] = [];
        const aggregator = new DockerProgressAggregator(
            [],
            (message, meta) => updates.push({message, meta}),
            0
        );

        aggregator.update('Building cyber-autoagent\n => [ 2/4] RUN apt-get update\n => exporting to image\n');

        expect(updates.map(update => update.message)).toEqual([
            'Building image… 1/4',
            'Building image… 4/4',
        ]);
        expect(updates.at(-1)?.meta).toEqual(expect.objectContaining({
            phase: 'build',
            ratio: 1,
            total: 4,
        }));
    });

    it('throttles repeated updates', () => {
        const updates: Update[] = [];
        let now = 1000;
        const nowSpy = jest.spyOn(Date, 'now').mockImplementation(() => now);
        const aggregator = new DockerProgressAggregator(
            ['api', 'db', 'worker'],
            (message, meta) => updates.push({message, meta}),
            500
        );

        try {
            aggregator.update('Pulling api');
            aggregator.update('Pull complete');
            now += 499;
            aggregator.update('Pull complete');
            now += 1;
            aggregator.update('Pull complete');
        } finally {
            nowSpy.mockRestore();
        }

        expect(updates.map(update => update.message)).toEqual([
            'Downloading images… 0/3 ready',
            'Downloading images… 3/3 ready',
        ]);
    });

    it('finalizes each completed phase with matching metadata', () => {
        const updates: Update[] = [];
        const aggregator = new DockerProgressAggregator(
            ['api'],
            (message, meta) => updates.push({message, meta}),
            0
        );

        aggregator.update('Status: Downloaded newer image for api\nCreating api ... done\nStarting api ... done');
        updates.length = 0;

        aggregator.finalize();

        expect(updates).toEqual([
            {
                message: 'Downloading images… 1/1 ready',
                meta: expect.objectContaining({phase: 'pull', ratio: 1}),
            },
            {
                message: 'Creating containers… 1/1',
                meta: expect.objectContaining({phase: 'create', ratio: 1}),
            },
            {
                message: 'Starting containers… 1/1',
                meta: expect.objectContaining({phase: 'start', ratio: 1}),
            },
        ]);
    });
});
