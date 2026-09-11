import React from 'react';
import {describe, expect, it, jest} from '@jest/globals';
import {TextDecoder, TextEncoder} from 'node:util';

Object.assign(globalThis, {TextDecoder, TextEncoder});

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
  useConfig: () => ({config: {modelProvider: 'bedrock', awsRegion: 'us-east-1'}}),
}));

const load = async () => {
  const mod: any = await import('../../../src/components/StreamDisplay.tsx');
  const {render} = await import('ink-testing-library');
  return {EventLine: mod.EventLine, render};
};

describe('EventLine preflight rendering', () => {
  it.each([
    ['pass', '🎯 PASS'],
    ['fail', '🎯 FAIL'],
    ['skip', '🎯 SKIP'],
  ])('renders the %s status and target details', async (status, label) => {
    const {EventLine, render} = await load();
    const frame = render(React.createElement(EventLine, {
      event: {
        type: 'preflight_check',
        status,
        target: 'service.test:443',
        target_type: 'network',
        checks: ['resolve', 'tcp_connect'],
        reason: status === 'fail' ? 'connection refused' : '',
      },
      animationsEnabled: false,
    })).lastFrame() || '';

    expect(frame).toContain(label);
    expect(frame).toContain('service.test:443');
    expect(frame).toContain('network');
    expect(frame).toContain('resolve, tcp_connect');
    if (status === 'fail') expect(frame).toContain('connection refused');
  });
});
