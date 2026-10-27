import {describe, expect, it} from '@jest/globals';
import {formatTechniqueEvent, techniqueEventTypes, techniqueEventSeverity} from '../../../src/utils/techniqueEventFormatting.js';
import {normalizeEvent} from '../../../src/services/events/normalize.js';
import {EventAggregator} from '../../../src/utils/eventAggregator.js';
import {PythonExecutionService} from '../../../src/services/PythonExecutionService.js';
import {DirectDockerService} from '../../../src/services/DirectDockerService.js';

const cases = [
  [{type: 'technique_build_progress', stage: 'request_alignment', status: 'completed',
    original_request: 'Validate applicability', adjusted_request: 'Develop applicability hypotheses'},
    'aligned request: Develop applicability hypotheses'],
  [{type: 'technique_sync_complete', added: 1, updated: 2, unchanged: 3}, '1 added, 2 updated, 3 unchanged'],
  [{type: 'technique_recommended', match_kind: 'semantic', title: 'Token validation'}, 'Token validation'],
  [{type: 'technique_build_progress', stage: 'critique', status: 'revision_requested', attempt: 2,
    reason: 'Missing controls'}, 'critique: revision requested, attempt 2 of 3'],
  [{type: 'technique_build_progress', stage: 'source_selection', status: 'completed', fetched_count: 3,
    selected_count: 2}, '3 documents fetched, 2 selected'],
  [{type: 'technique_resolved', title: 'JWT validation', outcome: 'reused'}, 'Technique reused: JWT validation'],
  [{type: 'technique_resolved', title: 'Source analysis', outcome: 'reused', applicability: 'conditional',
    unmet_prerequisite_count: 2}, '(conditional), 2 unmet prerequisites'],
  [{type: 'technique_resolved', title: 'Source analysis', outcome: 'generated', applicability: 'uncertain'},
    '(uncertain)'],
  [{type: 'technique_evaluated', score: 1, reason: 'Helpful controls'}, 'helpful (+1)'],
  [{type: 'technique_evaluated', score: 0}, 'neutral (0)'],
  [{type: 'technique_evaluated', score: -1}, 'unhelpful (-1)'],
  [{type: 'technique_deactivated', revision_id: 'revision'}, 'Technique deactivated: revision'],
  [{type: 'technique_warning', stage: 'export', diagnostic: 'Snapshot unavailable', retry_pending: true},
    'Snapshot unavailable (retry pending)'],
] as const;

describe('technique lifecycle presentation', () => {
  it.each(cases)('formats %o identically for console and TUI', (event, expected) => {
    expect(formatTechniqueEvent(event)).toContain(expected);
  });

  it('shows validation failures and diagnostic paths without displaying drafts', () => {
    const event = {type: 'technique_build_progress', stage: 'author_schema', status: 'failed',
      attempt: 3, reason: 'metadata.title: missing', snapshot_path: 'techniques/builds/build/attempt-3.json',
      response: 'PRIVATE DRAFT'};
    const message = formatTechniqueEvent(event)!;
    expect(message).toContain('author schema: failed, attempt 3 of 3');
    expect(message).toContain('metadata.title: missing');
    expect(message).toContain('[techniques/builds/build/attempt-3.json]');
    expect(message).not.toContain('PRIVATE DRAFT');
    expect(techniqueEventSeverity(event)).toBe('warning');
    expect(techniqueEventSeverity({...event, status: 'revision_requested'})).toBe('info');
  });

  it('handles optional fields, unrecognized events, and terminal control characters', () => {
    expect(formatTechniqueEvent({type: 'unknown'})).toBeNull();
    expect(formatTechniqueEvent({type: 'technique_sync_complete'})).toContain('0 added');
    expect(formatTechniqueEvent({type: 'technique_recommended'})).toContain('recommended');
    const event = {type: 'technique_resolved', outcome: 'generated', title: '\u001b[31m' + 'x'.repeat(3000),
      task_uid: 'network-task', snapshot_path: 'techniques/revision.produced.md', document: 'PRIVATE DOCUMENT'};
    const result = formatTechniqueEvent(event)!;
    expect(result).toContain('[network-task]');
    expect(result).toContain('[techniques/revision.produced.md]');
    expect(result).not.toContain('\u001b');
    expect(result).not.toContain('PRIVATE DOCUMENT');
    expect(result.length).toBeLessThan(1200);
  });

  it('shows timing, cumulative feedback, and shared warning severity', () => {
    expect(formatTechniqueEvent({type: 'technique_build_progress', stage: 'research', status: 'completed',
      elapsed_seconds: 2.25})).toContain('2.3s');
    expect(formatTechniqueEvent({type: 'technique_evaluated', score: -1, sentiment_count: 3,
      sentiment_mean: -2 / 3})).toContain('mean -0.67 across 3 uses');
    expect(techniqueEventSeverity({type: 'technique_warning'})).toBe('warning');
    expect(techniqueEventSeverity({type: 'technique_deactivated'})).toBe('warning');
    expect(techniqueEventSeverity({type: 'technique_resolved'})).toBe('info');
  });

  it('shows component and research round correlation in shared output', () => {
    expect(formatTechniqueEvent({type: 'technique_build_progress', stage: 'source_selection',
      status: 'completed', component_id: 'network', research_round: 2}))
      .toContain('[component network] [research round 2]');
  });

  it.each([
    ['python', () => new PythonExecutionService(), 'processOutputStream'],
    ['docker', () => new DirectDockerService(), 'parseEvents'],
  ] as const)('preserves backend envelopes through %s parsing and display aggregation', (_name, make, method) => {
    const service: any = make();
    const received: any[] = [];
    service.on('event', (event: any) => received.push(event));
    for (const type of techniqueEventTypes) {
      const envelope = {type, operation_id: 'op', task_uid: 'filesystem-task', activity_id: 'build-1',
        id: `op-${type}`, timestamp: '2026-10-26T12:00:00Z', future_field: 'preserved'};
      const wire = `__CYBER_EVENT__${JSON.stringify(envelope)}__CYBER_EVENT_END__`;
      service[method](wire.slice(0, 20));
      service[method](wire.slice(20));
    }
    const aggregator = new EventAggregator();
    const displayed = received.flatMap(event => aggregator.processEvent(normalizeEvent(event)));
    expect(displayed).toHaveLength(techniqueEventTypes.length);
    expect(displayed.map(event => event.type)).toEqual(techniqueEventTypes);
    expect(displayed.every(event => (event as any).task_uid === 'filesystem-task')).toBe(true);
    expect(displayed.every(event => (event as any).future_field === 'preserved')).toBe(true);
  });
});
