import React from 'react';
import {afterEach, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const config = {}; const detect = jest.fn<any>(); let selectProps: any; let backHandler: any;
jest.unstable_mockModule('ink', () => ({Box: ({children}: any) => <div>{children}</div>, Text: ({children}: any) => <span>{children}</span>, useInput: (handler: any) => {backHandler = handler;}}));
jest.unstable_mockModule('ink-spinner', () => ({default: () => <span>detecting</span>}));
jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({useConfig: () => ({config})}));
jest.unstable_mockModule('../../../src/services/DeploymentDetector.js', () => ({DeploymentDetector: {getInstance: () => ({detectDeployments: detect})}}));
jest.unstable_mockModule('../../../src/components/shared/RadioSelect.js', () => ({RadioSelect: (props: any) => {selectProps = props; return <span>{props.items.map((i: any) => i.label + props.renderBadge(i.badge)).join('|')}</span>;}}));
const {DeploymentSelectionScreen} = await import('../../../src/components/setup/DeploymentSelectionScreen.js');
afterEach(() => jest.clearAllMocks());
it.each([true, false])('handles detection success=%s and mode selection', async success => {
  const columns = process.stdout.columns; process.stdout.columns = 0;
  if (success) detect.mockResolvedValue({availableDeployments: [{mode: 'local-cli', isHealthy: true}, {mode: 'single-container', isHealthy: false}]});
  else detect.mockRejectedValue(new Error('offline'));
  let view!: ReturnType<typeof TestRenderer.create>; const onSelect = jest.fn(); const onBack = jest.fn();
  try {
    await act(async () => {view = TestRenderer.create(<DeploymentSelectionScreen onSelect={onSelect} onBack={onBack}/>);});
    if (success) expect(document.body.textContent).toContain('Active deployments detected: Local CLI');
    else expect(document.body.textContent).not.toContain('detecting');
    act(() => {selectProps.onSelect('full-stack'); backHandler('', {escape: true}); backHandler('x', {});});
    expect(onSelect).toHaveBeenCalledWith('full-stack'); expect(onBack).toHaveBeenCalledTimes(1);
    act(() => view.unmount());
  } finally {process.stdout.columns = columns;}
});
