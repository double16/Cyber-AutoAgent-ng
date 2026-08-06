/** Render typed memory and retrieval headers. */
import { describe, it, expect, jest, afterEach } from '@jest/globals';
import React from 'react';
import {TextDecoder, TextEncoder} from 'node:util';

Object.assign(globalThis, {TextDecoder, TextEncoder});

async function importEventLine() {
  jest.resetModules();
  const mod: any = await import('../../../src/components/StreamDisplay.tsx');
  const {render} = await import('ink-testing-library');
  return {EventLine: mod.EventLine as React.FC<any>, render};
}

describe('EventLine memory formatting', () => {
  afterEach(() => jest.resetModules());

  it('shows observation storage with content and artifacts', async () => {
    const {EventLine, render} = await importEventLine();
    const event = {
      type: 'tool_start',
      tool_name: 'store_observation',
      tool_input: { content: 'note: sql injection vector at /search', artifacts: ['response.txt'] }
    };
    const { lastFrame } = render(React.createElement(EventLine, { event, animationsEnabled: false }));
    const out = lastFrame();
    expect(out).toMatch(/tool:\s+store_observation/i);
    expect(out).toMatch(/storing observation/i);
    expect(out).toMatch(/1 artifact/i);
    expect(out).toMatch(/sql injection/i);
  });

  it('shows retrieve action with query preview', async () => {
    const {EventLine, render} = await importEventLine();
    const event = {
      type: 'tool_start',
      tool_name: 'memory_retrieve',
      tool_input: { query: 'find: injection' }
    };
    const { lastFrame } = render(React.createElement(EventLine, { event, animationsEnabled: false }));
    const out = lastFrame();
    expect(out).toMatch(/tool:\s+memory_retrieve/i);
    expect(out).toMatch(/action:\s+retrieving/i);
    expect(out).toMatch(/find: injection/i);
  });

  it('shows a finding as pending verification', async () => {
    const {EventLine, render} = await importEventLine();
    const event = {
      type: 'tool_start',
      tool_name: 'store_finding',
      tool_input: { title: 'IDOR', severity: 'HIGH', target: '/users/2', artifacts: [] }
    };
    const { lastFrame } = render(React.createElement(EventLine, { event, animationsEnabled: false }));
    const out = lastFrame();
    expect(out).toMatch(/tool:\s+store_finding/i);
    expect(out).toMatch(/submitting finding for verification/i);
    expect(out).toMatch(/severity:\s+HIGH/i);
  });
});
