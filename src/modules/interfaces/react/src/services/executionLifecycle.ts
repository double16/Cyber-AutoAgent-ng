import type {ExecutionHandle, ExecutionService} from './ExecutionService.js';

export interface StopExecutionOptions {
    executionHandle?: ExecutionHandle | null;
    executionService?: ExecutionService | null;
    cleanup?: boolean;
    removeListeners?: boolean;
    skipStop?: boolean;
    stopTimeoutMs?: number;
}

const withTimeout = async (promise: Promise<void>, timeoutMs: number): Promise<void> => {
    let timeout: NodeJS.Timeout | undefined;

    try {
        await Promise.race([
            promise,
            new Promise<void>((_, reject) => {
                timeout = setTimeout(() => reject(new Error(`Timed out stopping execution after ${timeoutMs}ms`)), timeoutMs);
                timeout.unref?.();
            }),
        ]);
    } finally {
        if (timeout) {
            clearTimeout(timeout);
        }
    }
};

export async function stopExecution({
                                        executionHandle,
                                        executionService,
                                        cleanup = false,
                                        removeListeners = false,
                                        skipStop = false,
                                        stopTimeoutMs = 5000,
                                    }: StopExecutionOptions): Promise<void> {
    let stopError: unknown;

    try {
        if (skipStop) {
            // Natural completion paths only need listener/resource cleanup.
        } else if (executionHandle) {
            await withTimeout(executionHandle.stop(), stopTimeoutMs);
        } else if (executionService) {
            await withTimeout(executionService.stop(), stopTimeoutMs);
        }
    } catch (error) {
        stopError = error;
    } finally {
        if (executionService) {
            if (removeListeners) {
                try {
                    executionService.removeAllListeners();
                } catch {
                }
            }
            if (cleanup) {
                try {
                    executionService.cleanup();
                } catch {
                }
            }
        }
    }

    if (stopError) {
        throw stopError;
    }
}
