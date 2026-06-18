import {describe, expect, it} from '@jest/globals';
import {EventType, type StreamEvent, type EventStats} from '../../../src/types/events.js';

describe('event type definitions', () => {
    it('exports stable runtime event type values used by stream rendering', () => {
        expect(EventType.AGENT_INITIALIZED).toBe('agent_initialized');
        expect(EventType.TOOL_START).toBe('tool_start');
        expect(EventType.SHELL_OUTPUT).toBe('shell_output');
        expect(EventType.SWARM_HANDOFF).toBe('swarm_handoff');
        expect(EventType.SPECIALIST_END).toBe('specialist_end');
        expect(EventType.USAGE_UPDATE).toBe('usage_update');
        expect(EventType.AGENT_COMPLETE).toBe('agent_complete');
        expect(Object.values(EventType)).toContain('connection_error');
    });

    it('supports representative stream event and statistics shapes at compile time', () => {
        const event: StreamEvent = {
            id: 'evt-1',
            timestamp: '2026-06-18T00:00:00.000Z',
            type: EventType.SYSTEM_STATUS,
            sessionId: 'session-1',
            level: 'info',
            message: 'ready',
        };
        const stats: EventStats = {
            totalEvents: 1,
            eventsByType: {
                ...Object.fromEntries(Object.values(EventType).map(type => [type, 0])),
                [EventType.SYSTEM_STATUS]: 1,
            } as Record<EventType, number>,
            errorCount: 0,
            averageLatency: 12,
        };

        expect(event.type).toBe(EventType.SYSTEM_STATUS);
        expect(stats.eventsByType[EventType.SYSTEM_STATUS]).toBe(1);
    });
});
