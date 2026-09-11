import { jest, beforeAll, afterAll } from '@jest/globals';

/**
 * Jest Test Setup
 * Configure test environment for React components
 */

// Mock console.error to reduce noise from React warnings in tests
const originalError = console.error;
beforeAll(() => {
  console.error = (...args: any[]) => {
    if (
      typeof args[0] === 'string' &&
      (args[0].includes('Warning: Invalid hook call') ||
       args[0].includes('Warning: Cannot update a component'))
    ) {
      return;
    }
    originalError.call(console, ...args);
  };
});

afterAll(() => {
  console.error = originalError;
});

// Set up global test environment (only in jsdom)
if (typeof (global as any).window !== 'undefined') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: jest.fn().mockImplementation(query => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: jest.fn(), // deprecated
      removeListener: jest.fn(), // deprecated
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    })),
  });

  // Mock process.stdout for Ink components (avoid interfering with node-pty tests)
  Object.defineProperty(process, 'stdout', {
    value: {
      columns: 80,
      rows: 24,
      isTTY: true,
      write: jest.fn(),
    },
  });
}

// Polyfill MessageChannel for react-dom/server in Node VM-based Jest envs
if (typeof (global as any).MessageChannel === 'undefined') {
  // Minimal stub; react-dom/server only needs the API shape for scheduling.
  (global as any).MessageChannel = class {
    port1 = { postMessage: () => {} };
    port2 = { postMessage: () => {} };
  } as any;
}

// Polyfill TextEncoder/TextDecoder for environments where they are missing
try {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const util = require('util');
  if (typeof (global as any).TextEncoder === 'undefined' && util.TextEncoder) {
    (global as any).TextEncoder = util.TextEncoder;
  }
  if (typeof (global as any).TextDecoder === 'undefined' && util.TextDecoder) {
    (global as any).TextDecoder = util.TextDecoder as any;
  }
} catch {
  // Best-effort only; if util isn't available, tests that depend on it may fail.
}

// Global test timeout
jest.setTimeout(10000);
