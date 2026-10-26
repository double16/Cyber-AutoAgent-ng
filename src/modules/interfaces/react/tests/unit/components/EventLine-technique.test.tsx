import React from 'react';
import {describe, expect, it, jest} from '@jest/globals';
import {TextDecoder, TextEncoder} from 'node:util';
import {formatTechniqueEvent} from '../../../src/utils/techniqueEventFormatting.js';

Object.assign(globalThis, {TextDecoder, TextEncoder});
jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
  useConfig: () => ({config: {modelProvider: 'bedrock', awsRegion: 'us-east-1'}}),
}));

describe('technique event rendering', () => {
  it.each([
    {type: 'technique_resolved', title: 'JWT validation', outcome: 'reused', task_uid: 'task-1'},
    {type: 'technique_warning', stage: 'evaluation', diagnostic: 'Feedback pending', retry_pending: true},
  ])('renders the shared console message for %o', async event => {
    const {EventLine} = await import('../../../src/components/StreamDisplay.js');
    const {render} = await import('ink-testing-library');
    const view = render(React.createElement(EventLine, {event: event as any, animationsEnabled: false}));
    expect(view.lastFrame()).toContain(formatTechniqueEvent(event));
    view.unmount();
  });
});
