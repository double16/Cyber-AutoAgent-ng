import { describe, it, expect } from '@jest/globals';
import React from 'react';
import { TextEncoder, TextDecoder } from 'util';
import { ConfigContext, defaultConfig } from '../../../src/contexts/ConfigContext.js';
import type { StreamEvent } from '../../../src/types/events.js';

if (typeof global.TextEncoder === 'undefined') {
  global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
  global.TextDecoder = TextDecoder;
}

const ensureMessageChannel = async () => {
  if (typeof global.MessageChannel === 'undefined') {
    const { MessageChannel } = await import('node:worker_threads');
    global.MessageChannel = MessageChannel;
  }
};

const loadEventLine = async () => {
  const [{ EventLine }, server] = await Promise.all([
    import('../../../src/components/StreamDisplay.tsx'),
    import('react-dom/server'),
  ]);
  return { EventLine, renderToStaticMarkup: server.renderToStaticMarkup };
};

const sanitize = (markup: string): string =>
  markup
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();

const renderEventLine = async (width: number, event: Partial<StreamEvent>) => {
  await ensureMessageChannel();
  const { EventLine, renderToStaticMarkup } = await loadEventLine();

  const providerValue = {
    config: { ...defaultConfig },
    isConfigLoading: false,
    updateConfig: () => {},
    saveConfig: async () => {},
    loadConfig: async () => {},
    resetToDefaults: () => {},
  };

  let originalColumns: number | undefined;
  if (typeof process.stdout?.columns === 'number') {
    originalColumns = process.stdout.columns;
  }

  try {
    Object.defineProperty(process.stdout, 'columns', { value: width, configurable: true });
  } catch {
    // Non-fatal in CI environments
  }

  const markup = renderToStaticMarkup(
    <ConfigContext.Provider value={providerValue}>
      <EventLine event={event as StreamEvent} animationsEnabled={false} configOverride={defaultConfig} />
    </ConfigContext.Provider>
  );

  if (originalColumns !== undefined) {
    Object.defineProperty(process.stdout, 'columns', { value: originalColumns, configurable: true });
  } else {
    delete (process.stdout as any).columns;
  }

  return sanitize(markup);
};

const widths = [60, 120];

describe('EventLine snapshot-style rendering', () => {
  it.each(widths)('renders shell tool header and command preview (width=%s)', async (width) => {
    const output = await renderEventLine(width as number, {
      type: 'tool_start',
      tool_name: 'shell',
      tool_input: { command: 'echo Hello' },
    });

    expect(output).toMatch(/tool:\s+shell/i);
    expect(output).toMatch(/echo Hello/i);
  });

  it.each(widths)('renders http_request tool header with method and url (width=%s)', async (width) => {
    const output = await renderEventLine(width as number, {
      type: 'tool_start',
      tool_name: 'http_request',
      tool_input: { method: 'GET', url: 'http://example.com' },
    });

    expect(output).toMatch(/tool:\s+http_request/i);
    expect(output).toMatch(/GET/i);
    expect(output).toMatch(/example.com/i);
  });

  it.each(widths)('renders reasoning block text (width=%s)', async (width) => {
    const output = await renderEventLine(width as number, {
      type: 'reasoning',
      content: 'Analyzing target for vulnerabilities...'
    });

    expect(output).toMatch(/reasoning/i);
    expect(output).toMatch(/Analyzing target/i);
  });

  it.each(widths)('renders agent progress header with agent metadata (width=%s)', async (width) => {
    const output = await renderEventLine(width as number, {
      type: 'progress_update',
      step: 20,
      progressPercent: 20,
      agent_name: 'recon_specialist',
      agent_sub_step: 2,
      agent_total_actions: 2,
    });

    expect(output).toMatch(/\[AGENT:/i);
    expect(output).toMatch(/RECON[ _]SPECIALIST/i);
    expect(output).toMatch(/ACTION\s+2/i);
    expect(output).toMatch(/PROGRESS\s+20%/i);
  });

  it.each(widths)('renders only the compact operation health score and band (width=%s)', async (width) => {
    const output = await renderEventLine(width as number, {
      type: 'progress_update',
      step: 20,
      progressPercent: 20,
      health: {
        status: 'available',
        score: 0.62,
        band: 'degraded',
        failure_count: 3,
        prediction: { expected_tasks: 12 },
      },
    } as any);

    expect(output).toContain('💛 62% DEGRADED');
    expect(output).not.toContain('failure_count');
    expect(output).not.toContain('expected_tasks');
  });
});
