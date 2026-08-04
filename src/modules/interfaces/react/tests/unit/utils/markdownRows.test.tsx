import {describe, expect, it} from '@jest/globals';
import {tokenizeMarkdown} from '../../../src/utils/markdownRows.js';

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
});
