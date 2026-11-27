import React from 'react';
import {expect, it} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
import {MarkdownRenderer, tokenizeMarkdown} from '../../../src/utils/markdownRows.js';
it('renders alternate markdown styles, deep headings, rules and numbered lists', () => {
  const content = '### Deep\n---\n***\n2. step\n__bold__ _italic_ *also* **strong** `code` [site](https://example.com)\n> quote\n| table |\n```\nraw\n```';
  let view!: ReturnType<typeof TestRenderer.create>;
  act(() => {view = TestRenderer.create(<MarkdownRenderer content={content}/>);});
  const html = document.body.innerHTML;
  expect(html).toContain('• Deep');
  expect(html).toContain('font-weight: bold');
  expect(html).toContain('site (https://example.com)');
  expect(html).toContain('2. step');
  expect(tokenizeMarkdown(content).some(row => row.kind === 'rule')).toBe(true);
  act(() => view.unmount());
});
