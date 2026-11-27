import React from 'react';
import {expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
import {Box, Text} from 'ink';
import {MaxSizedBox} from '../../../src/components/MaxSizedBox.js';
import {DeploymentWarning} from '../../../src/components/DeploymentWarning.js';
import {WelcomeScreen} from '../../../src/components/setup/WelcomeScreen.js';
import {TokenInput} from '../../../src/components/TokenInput.js';
import {Header} from '../../../src/components/Header.js';
import {themeManager} from '../../../src/themes/theme-manager.js';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
it('renders bounded output with singular indicators in both directions and ignores non-lines', () => {
  let view!: ReturnType<typeof TestRenderer.create>;
  for (const overflowDirection of ['top', 'bottom'] as const) {
    act(() => {view = TestRenderer.create(<MaxSizedBox maxWidth={20} maxHeight={2} additionalHiddenLinesCount={1} overflowDirection={overflowDirection}>ignored<Box><Text>A</Text></Box></MaxSizedBox>);});
    expect(document.body.textContent).toContain('1 line hidden');
    act(() => view.unmount());
  }
});
it('suppresses warnings when every deployment is configured', () => {
  let view!: ReturnType<typeof TestRenderer.create>;
  act(() => {view = TestRenderer.create(<DeploymentWarning configuredMode="local-cli" activeDeployments={[{mode: 'local-cli'}, {mode: 'local-cli'}] as any}/>);});
  expect(view.toJSON()).toBeNull(); act(() => view.unmount());
});
it('uses fallback screen widths and ignores unrelated welcome input', () => {
  const columns = process.stdout.columns;
  const next = jest.fn(); const skip = jest.fn();
  let view!: ReturnType<typeof TestRenderer.create>;
  try {
    process.stdout.columns = 0;
    act(() => {view = TestRenderer.create(<WelcomeScreen onContinue={next} onSkip={skip}/>);});
    act(() => (globalThis as any).__inkInputHandler('x', {}));
    expect(next).not.toHaveBeenCalled(); expect(skip).not.toHaveBeenCalled();
    act(() => view.unmount());
  } finally {process.stdout.columns = columns;}
});
it('blinks token cursor and clears its timer when unmounted', () => {
  jest.useFakeTimers(); let view!: ReturnType<typeof TestRenderer.create>;
  try {
    act(() => {view = TestRenderer.create(<TokenInput onSubmit={jest.fn()}/>);});
    expect(document.body.textContent).toContain('█');
    act(() => jest.advanceTimersByTime(500));
    expect(document.body.textContent).not.toContain('█');
    act(() => view.unmount()); expect(jest.getTimerCount()).toBe(0);
  } finally {jest.useRealTimers();}
});
it('renders the ASCII header without a gradient or stdout width', () => {
  const columns = process.stdout.columns;
  jest.spyOn(themeManager, 'shouldUseGradient').mockReturnValue(false);
  let view!: ReturnType<typeof TestRenderer.create>;
  try {
    process.stdout.columns = 0;
    act(() => {view = TestRenderer.create(<Header terminalWidth={100} nightly exitNotice/>);});
    expect(document.body.textContent).toContain('NIGHTLY');
    expect(document.body.textContent).toContain('Exiting');
    act(() => view.unmount());
  } finally {process.stdout.columns = columns; jest.restoreAllMocks();}
});
