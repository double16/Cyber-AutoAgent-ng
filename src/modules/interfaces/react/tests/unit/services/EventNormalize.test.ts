import {normalizeEvent} from '../../../src/services/events/normalize.js';
import {describe, expect, it} from '@jest/globals';

const long = (length: number) => 'x'.repeat(length);

describe('normalizeEvent source coverage', () => {
    it('returns non-object events unchanged', () => {
        expect(normalizeEvent(null as any)).toBeNull();
        expect(normalizeEvent('raw' as any)).toBe('raw');
    });

    it('normalizes top-level timestamps and clamps common string fields', () => {
        const event = normalizeEvent({
            type: 'output',
            timestamp: 0,
            content: long(32770),
            metadata: {nested: long(32770)},
        });

        expect(event.timestamp).toBe('1970-01-01T00:00:00.000Z');
        expect(event.content).toContain('truncated 2 chars');
        expect(event.metadata.nested).toContain('truncated 2 chars');
    });

    it('normalizes specialist events', () => {
        expect(normalizeEvent({
            type: 'specialist_start',
            name: 'auth',
            artifact_paths: ['a.json'],
        })).toEqual(expect.objectContaining({
            specialist: 'auth',
            artifactPaths: ['a.json'],
        }));

        expect(normalizeEvent({
            type: 'specialist_progress',
            gate: '2',
            total_gates: '5',
            tool: 123,
        })).toEqual(expect.objectContaining({
            gate: 2,
            totalGates: 5,
            tool: '123',
        }));

        expect(normalizeEvent({
            type: 'specialist_end',
            result: {
                validation_status: 'passed',
                severity_max: 'high',
                failed_gates: ['evidence'],
            },
        }).result).toEqual(expect.objectContaining({
            validationStatus: 'passed',
            severityMax: 'high',
            failedGates: ['evidence'],
        }));
    });

    it('normalizes shell tool starts and creates stable ids', () => {
        const event = normalizeEvent({
            type: 'tool_start',
            tool_name: 'shell',
            timestamp: 1000,
            args: {command: '[{"cmd":"echo one\\ntwo"}]'},
        });

        expect(event.args).toBeUndefined();
        expect(event.tool_name).toBe('shell');
        expect(event.tool_input.command).toEqual([{cmd: 'echo one\ntwo'}]);
        expect(event.toolId).toBe('shell-1');
    });

    it('keeps malformed JSON command inputs displayable', () => {
        const shell = normalizeEvent({
            type: 'tool_start',
            tool_name: 'shell',
            timestamp: 'not-a-date',
            args: {command: '[{\"cmd\":\"unterminated\"'},
        });

        expect(shell.tool_input.command).toEqual(['[{\"cmd\":\"unterminated\"']);
        expect(shell.toolId).toMatch(/^shell-\d+$/);

        const stringInput = normalizeEvent({
            type: 'tool_start',
            tool_name: 'http_request',
            tool_input: '{"method":"put","url":"/api"}',
        });
        expect(stringInput.tool_input).toEqual(expect.objectContaining({
            method: 'PUT',
            url: '/api',
        }));

        const unknownStringInput = normalizeEvent({
            type: 'tool_start',
            tool_name: 'custom_tool',
            tool_input: '[1,2,3]',
        });
        expect(unknownStringInput.tool_input).toEqual([1, 2, 3]);
    });

    it('normalizes common tool input shapes', () => {
        expect(normalizeEvent({
            type: 'tool_start',
            tool_name: 'http_request',
            tool_input: {method: 'post', url: 123},
            timestamp: '2024-01-01T00:00:00.000Z',
        }).tool_input).toEqual(expect.objectContaining({
            method: 'POST',
            url: '123',
        }));

        expect(normalizeEvent({
            type: 'tool_start',
            tool_name: 'file_write',
            tool_input: {path: 12, content: false},
        }).tool_input).toEqual(expect.objectContaining({
            path: '12',
            content: 'false',
        }));
    });

    it('omits heavy editor and python_repl payloads', () => {
        const editor = normalizeEvent({
            type: 'tool_start',
            tool_name: 'editor',
            tool_input: {
                path: 42,
                command: 'write',
                file_text: 'line1\nline2',
            },
        });

        expect(editor.tool_input.file_text).toBeUndefined();
        expect(editor.tool_input).toEqual(expect.objectContaining({
            path: '42',
            command: 'write',
            file_text_length: 11,
            file_text_lines: 2,
            file_text_preview: 'line1\nline2',
            file_text_omitted: true,
        }));

        const repl = normalizeEvent({
            type: 'tool_start',
            tool_name: 'python_repl',
            tool_input: {code: long(2500)},
        });

        expect(repl.tool_input.code).toBeUndefined();
        expect(repl.tool_input.code_preview).toHaveLength(1000);
        expect(repl.tool_input.code_length).toBe(2500);
    });

    it('preserves small editor and python payloads without adding preview metadata', () => {
        const editor = normalizeEvent({
            type: 'tool_start',
            tool_name: 'editor',
            tool_input: {
                file_text: 42,
            },
        });

        expect(editor.tool_input.file_text).toBeUndefined();
        expect(editor.tool_input.file_text_preview).toBeUndefined();
        expect(editor.tool_input.file_text_length).toBeUndefined();

        const repl = normalizeEvent({
            type: 'tool_start',
            tool_name: 'python_repl',
            tool_input: {code: 'print(1)'},
        });
        expect(repl.tool_input.code).toBe('print(1)');
        expect(repl.tool_input.code_preview).toBeUndefined();
    });

    it('summarizes prompt optimizer overlays', () => {
        const event = normalizeEvent({
            type: 'tool_start',
            tool_name: 'prompt_optimizer',
            tool_input: {
                Action: 'refine',
                note: long(450),
                current_step: '3',
                expires_after_steps: '6',
                overlay: JSON.stringify({
                    payload: {
                        directives: ['a', 'b', 'c', 'd', 'e'],
                        trajectory: {reason: long(32770)},
                        metadata: {source: 'reviewer'},
                    },
                }),
            },
        });

        expect(event.tool_input).toEqual(expect.objectContaining({
            action: 'refine',
            current_step: 3,
            expires_after_steps: 6,
            directives: 'a, b, c, d, ... (+1 more)',
            metadata: {source: 'reviewer'},
        }));
        expect(event.tool_input.note).toContain('truncated 50 chars');
        expect(event.tool_input.trajectory.reason).toContain('truncated');
    });

    it('handles prompt optimizer defaults and invalid overlays', () => {
        expect(normalizeEvent({
            type: 'tool_start',
            tool_name: 'prompt_optimizer',
            tool_input: {overlay: '{bad json'},
        }).tool_input).toEqual({action: 'apply'});

        expect(normalizeEvent({
            type: 'tool_start',
            tool_name: 'prompt_optimizer',
            tool_input: {
                trigger: 'drift',
                reviewer: 'critic',
                context: 'step context',
                prompt: 'new prompt',
                overlay: {
                    directives: [' keep ', '', 'evidence first'],
                },
            },
        }).tool_input).toEqual(expect.objectContaining({
            action: 'apply',
            trigger: 'drift',
            reviewer: 'critic',
            context: 'step context',
            prompt: 'new prompt',
            directives: 'keep, evidence first',
        }));
    });

    it('normalizes command, tool_output, and prompt_change events', () => {
        expect(normalizeEvent({
            type: 'command',
            content: '{"command":"ls -la"}',
        }).content).toBe('ls -la');

        expect(normalizeEvent({
            type: 'tool_output',
            output: 'hello',
        })).toEqual(expect.objectContaining({
            output: {text: 'hello'},
            status: 'success',
        }));

        const promptChange = normalizeEvent({
            type: 'prompt_change',
            overlay: '{"payload":{"text":"ok"}}',
            directives: [long(32770)],
            summary: 'summary',
            note: 'note',
        });

        expect(promptChange.overlay).toEqual({payload: {text: 'ok'}});
        expect(promptChange.directives[0]).toContain('truncated 2 chars');
        expect(promptChange.summary).toBe('summary');
        expect(promptChange.note).toBe('note');
    });

    it('keeps invalid command and prompt-change payloads stable', () => {
        expect(normalizeEvent({
            type: 'command',
            content: '{"notCommand":"ls"}',
        }).content).toBe('{"notCommand":"ls"}');

        const promptChange = normalizeEvent({
            type: 'prompt_change',
            overlay: '{bad json',
            directives: ['ok'],
            summary: long(32770),
            note: long(32770),
        });

        expect(promptChange.overlay).toBeUndefined();
        expect(promptChange.summary).toContain('truncated 2 chars');
        expect(promptChange.note).toContain('truncated 2 chars');
    });
});
