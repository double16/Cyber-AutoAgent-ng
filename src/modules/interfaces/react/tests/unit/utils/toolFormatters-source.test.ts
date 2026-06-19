import {
  formatDuration,
  formatGenericToolInput,
  formatToolInput,
  getToonPlanPreview,
  toolFormatters,
} from '../../../src/utils/toolFormatters.js';
import {describe, expect, it, jest} from '@jest/globals';

describe('toolFormatters source coverage', () => {
    it('formats durations from seconds, milliseconds, and dates', () => {
        const nowSpy = jest.spyOn(Date, 'now').mockReturnValue(10_000);

        try {
            expect(formatDuration(59)).toBe('59s');
            expect(formatDuration(61)).toBe('1m 1s');
            expect(formatDuration(3600, false)).toBe('1h');
            expect(formatDuration(3661, false)).toBe('1h 1m');
            expect(formatDuration(1500)).toBe('1s');
            expect(formatDuration(1500, false)).toBe('25m');
            expect(formatDuration(new Date(5_000))).toBe('5s');
        } finally {
            nowSpy.mockRestore();
        }
    });

    it('formats generic inputs across primitives, arrays, objects, and JSON strings', () => {
        expect(formatGenericToolInput(null)).toBe('');
        expect(formatGenericToolInput(false)).toBe('false');
        expect(formatGenericToolInput('[bad json')).toBe('[bad json');
        expect(formatGenericToolInput([])).toBe('[0 items]');
        expect(formatGenericToolInput([1, 2, 3, 4])).toBe('items: 1, 2, 3 (+1 more)');
        expect(formatGenericToolInput({})).toBe('{}');
        expect(formatGenericToolInput({nested: {a: 1, b: 2, c: 3, d: 4}})).toBe('nested: {a, b, c…}');
        expect(formatGenericToolInput({value: 0})).toBe('value: 0');
        expect(formatGenericToolInput('{"target":"example.com"}')).toBe('target: example.com');
        expect(formatGenericToolInput(['alpha', ['nested'], {a: 1, b: 2, c: 3, d: 4}]))
            .toBe('items: alpha, [1 items], {a, b, c…}');
        expect(formatGenericToolInput({items: [1, 2, 3]})).toBe('items: [3 items]');
        expect(formatGenericToolInput({a: 'one', b: {c: 1, d: 2, e: 3}, c: true}))
            .toBe('a: one | b: {c, d…} | c: true');
        expect(formatGenericToolInput({a: 1, b: 2, c: 3, d: 4, e: 5, f: 6}))
            .toContain('(+');
    });

    it('formats mem0 and plan tools', () => {
        expect(toolFormatters.mem0_list({query: 'findings'})).toBe('list memories');
        expect(toolFormatters.mem0_retrieve({query: 'findings'})).toBe('retrieve memories');
        expect(toolFormatters.mem0_store({
            content: JSON.stringify({memory: 'Stored memory text'}),
        })).toContain('Stored memory text');
        expect(toolFormatters.mem0_store({
            content: JSON.stringify({results: [{memory: 'First result memory'}]}),
        })).toContain('First result memory');
        expect(toolFormatters.mem0_get({query: {target: 'example.com'}})).toContain('target');
        expect(toolFormatters.mem0_get({query: '{bad json'})).toContain('{bad json');

        const toon = `plan_overview[1]{objective,current_phase,total_phases}:
  Test portal,2,4
plan_phases[4]{id,title,status,criteria}:
  1,Recon,done,map
  2,Auth,active,test login`;

        expect(getToonPlanPreview(toon)).toBe('Test portal (Phase 2/4 – Auth)');
        expect(formatToolInput('store_plan', {plan: toon})).toContain('Test portal');
        expect(getToonPlanPreview('not a toon plan')).toBeNull();
        expect(getToonPlanPreview('plan_overview[1]{objective,current_phase,total_phases}:\nOnly objective')).toBeNull();
        expect(formatToolInput('get_plan', {plan: {objective: 'object plan'}})).toContain('object plan');
    });

    it('formats shell input variants with flags and extras', () => {
        expect(toolFormatters.shell({
            command: JSON.stringify([{command: 'whoami'}, {args: ['ls', '-la']}]),
            parallel: true,
            ignore_errors: true,
            non_interactive: true,
            timeout: 30,
            cwd: '/tmp',
        })).toBe('Commands: whoami | ls -la | parallel, ignore_errors, non_interactive | timeout: 30s | cwd: /tmp');

        expect(toolFormatters.shell({command: {cmd: 'id'}})).toContain('id');
        expect(toolFormatters.shell({command: {value: 'hostname'}})).toContain('hostname');
        expect(toolFormatters.shell({command: {args: ['echo', 'ok']}})).toContain('echo ok');
        expect(toolFormatters.shell({command: {unexpected: 'shape'}})).toContain('unexpected');
        expect(toolFormatters.shell({command: '[bad json'})).toBe('Commands: [bad json');
        expect(toolFormatters.shell({command: ['printf', null, undefined, 7]})).toBe('Commands: printf | 7');
        expect(toolFormatters.shell({command: null})).toBe('Commands: (none)');
    });

    it('formats browser, file, report, handoff, task, and stop tools', () => {
        expect(formatToolInput('http_request', {method: 'POST', url: 'https://example.com'}))
            .toBe('method: POST | url: https://example.com');
        expect(formatToolInput('browser_set_headers', {headers: {Authorization: 'Bearer x'}}))
            .toContain('Authorization');
        expect(formatToolInput('browser_set_headers', {})).toBe('headers: {}');
        expect(formatToolInput('browser_goto_url', {url: 'https://example.com'})).toBe('url: https://example.com');
        expect(formatToolInput('browser_goto_url', {})).toBe('url: unknown');
        expect(formatToolInput('browser_perform_action', {action: 'click'})).toBe('action: click');
        expect(formatToolInput('browser_perform_action', {})).toBe('action: unknown');
        expect(formatToolInput('browser_observe_page', {instruction: 'find login'})).toBe('instruction: find login');
        expect(formatToolInput('browser_observe_page', {})).toBe('instruction: unknown');
        expect(formatToolInput('browser_evaluate_js', {expression: 'document.title'})).toBe('expression: document.title');
        expect(formatToolInput('browser_evaluate_js', {})).toBe('expression: unknown');
        expect(formatToolInput('file_write', {path: '/tmp/a.txt', content: 'abc'})).toBe('path: /tmp/a.txt | 3 chars');
        expect(formatToolInput('file_write', {})).toBe('path: unknown');
        expect(formatToolInput('editor', {
            command: 'replace',
            path: 'a.ts',
            content: 'abcdef'
        })).toBe('replace: a.ts | 6 chars');
        expect(formatToolInput('editor', {})).toBe('edit: ');
        expect(formatToolInput('report_generator', {target: 'example.com', report_type: 'markdown'}))
            .toBe('target: example.com | type: markdown');
        expect(formatToolInput('report_generator', {type: 'json'}))
            .toBe('target: unknown | type: json');
        expect(formatToolInput('report_generator', {}))
            .toBe('target: unknown | type: general');
        expect(formatToolInput('handoff_to_agent', {agent: 'auth', message: 'check login'}))
            .toBe('target: auth | message: check login');
        expect(formatToolInput('handoff_to_agent', {target_agent: 'web'}))
            .toBe('target: web | message: ');
        expect(formatToolInput('handoff_to_agent', {}))
            .toBe('target: unknown | message: ');
        expect(formatToolInput('load_tool', {tool_name: 'scanner', path: '/tools/scanner', description: 'scan things'}))
            .toBe('loading: scanner | path: /tools/scanner | scan things');
        expect(formatToolInput('load_tool', {tool: 'scanner'})).toBe('loading: scanner');
        expect(formatToolInput('load_tool', {})).toBe('loading: unknown');
        expect(formatToolInput('create_tasks', {tasks: [{title: 'one'}, {title: 'two'}]}))
            .toBe('create 2 tasks:\n• one\n• two');
        expect(formatToolInput('create_tasks', {tasks: []})).toBe('no tasks to create');
        expect(formatToolInput('create_tasks', {})).toBe('invalid tasks input: expected an array');
        expect(formatToolInput('stop', {reason: 'done'})).toBe('done');
    });

    it('formats python code previews and unknown tools', () => {
        expect(formatToolInput('python_repl', {code: 'print(1)'})).toBe('code:\nprint(1)');
        expect(formatToolInput('python_repl', {code: Array.from({length: 20}, (_, i) => `print(${i})`).join('\n')}))
            .toContain('...');
        expect(formatToolInput('unknown_tool', {target: 'example.com'})).toBe('target: example.com');
    });
});
