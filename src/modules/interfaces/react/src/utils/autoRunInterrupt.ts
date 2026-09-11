export interface InterruptInput {
  isTTY?: boolean;
  isRaw?: boolean;
  readableFlowing?: boolean | null;
  setRawMode?: (enabled: boolean) => void;
  resume?: () => void;
  pause?: () => void;
  on: (event: 'data', listener: (data: Buffer | string) => void) => unknown;
  off: (event: 'data', listener: (data: Buffer | string) => void) => unknown;
}

export const installAutoRunInterruptFallback = (
  onInterrupt: () => void,
  input: InterruptInput = process.stdin,
): (() => void) => {
  if (!input.isTTY || typeof input.setRawMode !== 'function') {
    return () => undefined;
  }

  const wasRaw = Boolean(input.isRaw);
  const wasFlowing = input.readableFlowing === true;
  const handleData = (data: Buffer | string) => {
    const bytes = Buffer.isBuffer(data) ? data : Buffer.from(data);
    if (bytes.includes(3)) onInterrupt();
  };

  input.setRawMode(true);
  input.on('data', handleData);
  input.resume?.();

  return () => {
    input.off('data', handleData);
    if (!wasRaw) input.setRawMode?.(false);
    if (!wasFlowing) input.pause?.();
  };
};
