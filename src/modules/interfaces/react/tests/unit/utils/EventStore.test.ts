import {EventStore} from '../../../src/utils/EventStore.js';

const event = (id: number): any => ({
    type: 'output',
    content: `event-${id}`,
    id: String(id),
});

describe('EventStore', () => {
    it('appends single events and batches in insertion order', () => {
        const store = new EventStore();

        store.append(event(1));
        store.appendBatch([event(2), event(3)]);

        expect(store.count).toBe(3);
        expect(store.toArray().map(item => item.content)).toEqual(['event-1', 'event-2', 'event-3']);
    });

    it('returns recent events across completed and active chunks', () => {
        const store = new EventStore();
        store.appendBatch(Array.from({length: 125}, (_, index) => event(index + 1)));

        expect(store.count).toBe(125);
        expect(store.getRecent(3).map(item => item.content)).toEqual(['event-123', 'event-124', 'event-125']);
        expect(store.getRecent(200)).toHaveLength(125);
    });

    it('returns ranges spanning chunk boundaries', () => {
        const store = new EventStore();
        store.appendBatch(Array.from({length: 130}, (_, index) => event(index)));

        expect(store.getRange(98, 103).map(item => item.content)).toEqual([
            'event-98',
            'event-99',
            'event-100',
            'event-101',
            'event-102',
        ]);
    });

    it('trims old chunks when max event count is exceeded', () => {
        const store = new EventStore(150);
        store.appendBatch(Array.from({length: 250}, (_, index) => event(index)));

        const all = store.toArray();
        expect(store.count).toBe(150);
        expect(all).toHaveLength(150);
        expect(all[0].content).toBe('event-100');
        expect(all.at(-1)?.content).toBe('event-249');
    });

    it('splits completed and active events', () => {
        const store = new EventStore();
        store.appendBatch(Array.from({length: 5}, (_, index) => event(index + 1)));

        expect(store.split(2)).toEqual({
            completed: [event(1), event(2), event(3)],
            active: [event(4), event(5)],
        });

        expect(store.split(10)).toEqual({
            completed: [],
            active: [event(1), event(2), event(3), event(4), event(5)],
        });
    });

    it('creates immutable snapshots and clears stored events', () => {
        const store = new EventStore();
        store.appendBatch([event(1), event(2)]);

        const snapshot = store.snapshot();
        expect(Object.isFrozen(snapshot)).toBe(true);
        expect(snapshot.map(item => item.content)).toEqual(['event-1', 'event-2']);

        store.clear();
        expect(store.count).toBe(0);
        expect(store.toArray()).toEqual([]);
    });
});
