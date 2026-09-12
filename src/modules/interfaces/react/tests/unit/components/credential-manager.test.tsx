import React from 'react';
import TestRenderer, { ReactTestRenderer, act } from '../test-renderer.js';
import { describe, expect, it, jest } from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let inputHandler: ((input: string, key: any) => void) | undefined;
jest.unstable_mockModule('ink', () => ({
  Box: ({ children }: any) => <div>{children}</div>,
  Text: ({ children }: any) => <span>{children}</span>,
  useInput: (handler: any) => { inputHandler = handler; },
}));
jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
  useConfig: () => ({ config: { outputDir: './outputs' } }),
}));
jest.unstable_mockModule('../../../src/themes/theme-manager.js', () => ({
  themeManager: { getCurrentTheme: () => ({ primary: 'blue', muted: 'gray' }) },
}));
jest.unstable_mockModule('ink-text-input', () => ({
  default: ({ value, onChange }: any) => <input value={value} onChange={(event: any) => onChange(event.target.value)} />,
}));

const text = (node: any): string => {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(text).join('');
  return text(node.children || []);
};

describe('CredentialManager', () => {
  it('renders metadata, queues only operation-managed credentials, and closes on escape', async () => {
    const { CredentialManager } = await import('../../../src/components/CredentialManager.js');
    const service = {
      list: jest.fn().mockResolvedValue({
        credentials: [{
          credential_id: 'cred-1', credential_type: 'api_key', role: 'reader', status: 'valid',
          origin: 'registered', management_policy: 'operation', target: 'https://app.example.test',
          history: [{ status: 'valid' }], rotation_requests: [],
        }],
      }),
      queueRotation: jest.fn().mockResolvedValue({ request: { maintenance_operation_id: 'OP_ROTATE' } }),
    };
    const onClose = jest.fn();
    let view!: ReactTestRenderer;
    await act(async () => {
      view = TestRenderer.create(<CredentialManager initialTarget="https://app.example.test" service={service as any} onClose={onClose} />);
    });
    expect(text(view.toJSON())).toContain('api_key reader');
    expect(text(view.toJSON())).not.toContain('secret');
    act(() => inputHandler?.('r', {}));
    expect(text(view.toJSON())).toContain('Rotation reason:');
    act(() => view.root.findByType('input').props.onChange({ target: { value: 'expiry' } }));
    await act(async () => {
      inputHandler?.('', { return: true });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(service.queueRotation).toHaveBeenCalledWith('https://app.example.test', 'cred-1', 'expiry', expect.anything());
    act(() => inputHandler?.('', { escape: true }));
    expect(onClose).toHaveBeenCalled();
  });

  it('reports loading failures for a selected target', async () => {
    const { CredentialManager } = await import('../../../src/components/CredentialManager.js');
    const service = {
      list: jest.fn().mockRejectedValue(new Error('database unavailable')),
      queueRotation: jest.fn(),
    };
    let view!: ReactTestRenderer;
    await act(async () => {
      view = TestRenderer.create(<CredentialManager initialTarget="https://app.example.test" service={service as any} onClose={jest.fn()} />);
    });
    expect(text(view.toJSON())).toContain('Unable to load credentials: database unavailable');
  });

  it('loads a target entered in the modal and supports navigation refresh', async () => {
    const { CredentialManager } = await import('../../../src/components/CredentialManager.js');
    const service = {
      list: jest.fn().mockResolvedValue({ credentials: [] }),
      queueRotation: jest.fn(),
    };
    let view!: ReactTestRenderer;
    await act(async () => {
      view = TestRenderer.create(<CredentialManager service={service as any} onClose={jest.fn()} />);
    });
    act(() => view.root.findByType('input').props.onChange({ target: { value: 'https://app.example.test' } }));
    await act(async () => {
      inputHandler?.('', { return: true });
      await Promise.resolve();
    });
    expect(service.list).toHaveBeenCalledWith('https://app.example.test', expect.anything());
    act(() => inputHandler?.('', { upArrow: true }));
    act(() => inputHandler?.('', { downArrow: true }));
    await act(async () => {
      inputHandler?.('l', {});
      await Promise.resolve();
    });
    expect(service.list).toHaveBeenCalledTimes(2);
  });

  it('rejects a user-managed credential without opening the rotation prompt', async () => {
    const { CredentialManager } = await import('../../../src/components/CredentialManager.js');
    const service = {
      list: jest.fn().mockResolvedValue({ credentials: [{
        credential_id: 'cred-user', credential_type: 'api_key', role: 'reader', status: 'valid',
        origin: 'provided', management_policy: 'user', history: [], rotation_requests: [],
      }] }),
      queueRotation: jest.fn(),
    };
    let view!: ReactTestRenderer;
    await act(async () => {
      view = TestRenderer.create(<CredentialManager initialTarget="https://app.example.test" service={service as any} onClose={jest.fn()} />);
    });
    act(() => inputHandler?.('r', {}));
    expect(text(view.toJSON())).toContain('User-provided credentials must be changed');
    expect(service.queueRotation).not.toHaveBeenCalled();
  });
});
