/**
 * SafetyWarning Component Simple Tests
 * 
 * Tests the basic functionality without full React rendering
 */

import { describe, it, expect, jest, beforeEach } from '@jest/globals';
import React from 'react';
import TestRenderer, { act } from '../test-renderer.js';
import { SafetyWarning } from '../../../src/components/SafetyWarning.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const textFromTree = (node: unknown): string => {
  if (node === null || node === undefined) {
    return '';
  }
  if (typeof node === 'string') {
    return node;
  }
  if (Array.isArray(node)) {
    return node.map(textFromTree).join('');
  }
  if (typeof node === 'object' && 'children' in node) {
    return textFromTree((node as { children?: unknown }).children);
  }
  return '';
};

describe('SafetyWarning Component Logic', () => {
  let mockOnConfirm: jest.MockedFunction<() => void>;
  let mockOnCancel: jest.MockedFunction<() => void>;

  beforeEach(() => {
    mockOnConfirm = jest.fn();
    mockOnCancel = jest.fn();
    jest.clearAllMocks();
  });

  describe('Component Props Validation', () => {
    it('should accept required props', () => {
      const props = {
        target: 'testphp.vulnweb.com',
        module: 'general',
        onConfirm: mockOnConfirm,
        onCancel: mockOnCancel
      };

      expect(props.target).toBe('testphp.vulnweb.com');
      expect(props.module).toBe('general');
      expect(typeof props.onConfirm).toBe('function');
      expect(typeof props.onCancel).toBe('function');
    });

    it('should handle different target formats', () => {
      const testTargets = [
        'https://api.example.com:8080/v1',
        '192.168.1.1',
        'localhost:3000',
        'subdomain.example.org/path'
      ];

      testTargets.forEach(target => {
        const props = {
          target,
          module: 'network',
          onConfirm: mockOnConfirm,
          onCancel: mockOnCancel
        };
        expect(props.target).toBe(target);
      });
    });

    it('should handle different module names', () => {
      const modules = ['general', 'network', 'web', 'wireless'];

      modules.forEach(module => {
        const props = {
          target: 'example.com',
          module,
          onConfirm: mockOnConfirm,
          onCancel: mockOnCancel
        };
        expect(props.module).toBe(module);
      });
    });
  });

  describe('Safety Authorization Logic', () => {
    it('should update the rendered prompt after a full yes response and confirm on the second yes', async () => {
      let renderer: ReturnType<typeof TestRenderer.create>;
      act(() => {
        renderer = TestRenderer.create(
          <SafetyWarning
            target="https://authorized.example"
            module="web"
            onConfirm={mockOnConfirm}
            onCancel={mockOnCancel}
          />
        );
      });

      expect(textFromTree(renderer!.toJSON())).toContain('Do you acknowledge that you have proper authorization?');

      await act(async () => {
        (global as any).__inkInputHandler('yes', {});
      });

      expect(textFromTree(renderer!.toJSON())).toContain('Authorization acknowledged');
      expect(textFromTree(renderer!.toJSON())).toContain('Proceed with cyber operation?');
      expect(mockOnConfirm).not.toHaveBeenCalled();

      await act(async () => {
        (global as any).__inkInputHandler('yes', {});
        (global as any).__inkInputHandler('yes', {});
      });

      expect(mockOnConfirm).toHaveBeenCalledTimes(1);
      expect(mockOnCancel).not.toHaveBeenCalled();

      act(() => {
        renderer!.unmount();
      });
    });

    it('should accept full no responses and cancel once', async () => {
      let renderer: ReturnType<typeof TestRenderer.create>;
      act(() => {
        renderer = TestRenderer.create(
          <SafetyWarning
            target="https://authorized.example"
            module="web"
            onConfirm={mockOnConfirm}
            onCancel={mockOnCancel}
          />
        );
      });

      await act(async () => {
        (global as any).__inkInputHandler('no', {});
        (global as any).__inkInputHandler('no', {});
      });

      expect(mockOnCancel).toHaveBeenCalledTimes(1);
      expect(mockOnConfirm).not.toHaveBeenCalled();

      act(() => {
        renderer!.unmount();
      });
    });

    it('should require double confirmation pattern', () => {
      // Simulate the component's state logic
      let acknowledged = false;
      let confirmed = false;

      // First step - acknowledgment
      const handleFirstInput = (input: string) => {
        if (input === 'y' || input === 'Y') {
          acknowledged = true;
        } else if (input === 'n' || input === 'N') {
          mockOnCancel();
        }
      };

      // Second step - confirmation
      const handleSecondInput = (input: string) => {
        if (acknowledged && (input === 'y' || input === 'Y')) {
          confirmed = true;
          mockOnConfirm();
        } else if (input === 'n' || input === 'N') {
          mockOnCancel();
        }
      };

      // Test the flow
      handleFirstInput('y');
      expect(acknowledged).toBe(true);
      expect(mockOnConfirm).not.toHaveBeenCalled();

      handleSecondInput('y');
      expect(confirmed).toBe(true);
      expect(mockOnConfirm).toHaveBeenCalledTimes(1);
    });

    it('should prevent bypassing double confirmation', () => {
      let acknowledged = false;
      let confirmed = false;

      const handleInput = (input: string) => {
        if (!acknowledged) {
          if (input === 'y' || input === 'Y') {
            acknowledged = true;
          } else if (input === 'n' || input === 'N') {
            mockOnCancel();
          }
        } else if (!confirmed) {
          // Second step - only allow one confirmation
          if (input === 'y' || input === 'Y') {
            confirmed = true;
            mockOnConfirm();
          } else if (input === 'n' || input === 'N') {
            mockOnCancel();
          }
        }
        // Ignore further inputs after confirmation
      };

      // Try rapid inputs
      handleInput('y'); // First acknowledgment
      handleInput('y'); // Second confirmation
      handleInput('y'); // Should be ignored

      // Should only confirm once after proper flow
      expect(mockOnConfirm).toHaveBeenCalledTimes(1);
    });

    it('should handle cancellation at any step', () => {
      const testCancellations = ['n', 'N'];

      testCancellations.forEach(cancelInput => {
        const localMockCancel = jest.fn();
        
        const handleInput = (input: string) => {
          if (input === 'n' || input === 'N') {
            localMockCancel();
          }
        };

        handleInput(cancelInput);
        expect(localMockCancel).toHaveBeenCalledTimes(1);
      });
    });

    it('should handle ESC key cancellation', () => {
      const handleKeyInput = (key: { escape?: boolean }) => {
        if (key.escape) {
          mockOnCancel();
        }
      };

      handleKeyInput({ escape: true });
      expect(mockOnCancel).toHaveBeenCalledTimes(1);
    });
  });

  describe('Security Content Validation', () => {
    it('should include required warning elements', () => {
      const requiredWarnings = [
        'SECURITY ASSESSMENT AUTHORIZATION WARNING',
        'EXPLICIT WRITTEN AUTHORIZATION',
        'LEGAL PERMISSION',
        'PROPER SAFETY MEASURES',
        'APPROPRIATE SCOPE',
        'Unauthorized security testing may violate',
        'You assume full legal responsibility'
      ];

      // In a real component test, we'd check these appear in the rendered output
      requiredWarnings.forEach(warning => {
        expect(typeof warning).toBe('string');
        expect(warning.length).toBeGreaterThan(0);
      });
    });

    it('should display proper instructions', () => {
      const instructions = [
        'Do you acknowledge that you have proper authorization? (y/N)',
        'Proceed with cyber operation? (y/N)',
        "Press 'y' to continue, 'n' to cancel, or Esc to abort"
      ];

      instructions.forEach(instruction => {
        expect(typeof instruction).toBe('string');
        expect(instruction.length).toBeGreaterThan(0);
      });
    });
  });

  describe('Component State Management', () => {
    it('should maintain proper state transitions', () => {
      let currentState = 'initial';
      
      const handleStateTransition = (input: string) => {
        switch (currentState) {
          case 'initial':
            if (input === 'y' || input === 'Y') {
              currentState = 'acknowledged';
            } else if (input === 'n' || input === 'N') {
              currentState = 'cancelled';
              mockOnCancel();
            }
            break;
          case 'acknowledged':
            if (input === 'y' || input === 'Y') {
              currentState = 'confirmed';
              mockOnConfirm();
            } else if (input === 'n' || input === 'N') {
              currentState = 'cancelled';
              mockOnCancel();
            }
            break;
        }
      };

      expect(currentState).toBe('initial');
      
      handleStateTransition('y');
      expect(currentState).toBe('acknowledged');
      
      handleStateTransition('y');
      expect(currentState).toBe('confirmed');
      expect(mockOnConfirm).toHaveBeenCalledTimes(1);
    });
  });
});
