import {describe, expect, it} from '@jest/globals';
import {TextDecoder, TextEncoder} from 'util';
import React from 'react';
import {renderMarkdownRow, tokenizeMarkdown} from '../../../src/utils/markdownRows.js';

if (typeof global.TextEncoder === 'undefined') global.TextEncoder = TextEncoder;
if (typeof global.TextDecoder === 'undefined') global.TextDecoder = TextDecoder as typeof global.TextDecoder;

describe('documentation Markdown rows', () => {
    it('formats block-level Markdown into terminal-friendly rows', () => {
        const rows = tokenizeMarkdown([
            '# Title',
            '',
            '**Important** and `command`',
            '- item',
            '> note',
            '```bash',
            'echo hello',
            '```',
            '| Name | Value |',
        ].join('\n'));

        expect(rows.map((row) => row.kind)).toEqual([
            'heading', 'normal', 'normal', 'normal', 'quote', 'rule', 'code', 'rule', 'table',
        ]);
        expect(rows[0]).toMatchObject({text: 'Title', level: 1});
        expect(rows[3].text).toBe('• item');
        expect(rows[6].text).toBe('echo hello');
    });

    it('supports nested list indentation and ordered lists', () => {
        const rows = tokenizeMarkdown('1. first\n  - second');

        expect(rows[0].text).toBe('1. first');
        expect(rows[1].text).toBe('  • second');
    });

    it('renders Markdown rows with terminal formatting components', async () => {
        const {render} = await import('ink-testing-library');
        const output = render(
            <>
                {tokenizeMarkdown('# Title\n\n**Important** and `command`\n> note').map((row, index) =>
                    renderMarkdownRow(row, String(index))
                )}
            </>
        ).lastFrame();

        expect(output).toContain('Title');
        expect(output).toContain('Important');
        expect(output).toContain('command');
        expect(output).toContain('│ note');
        expect(output).not.toContain('**Important**');
    });
});
