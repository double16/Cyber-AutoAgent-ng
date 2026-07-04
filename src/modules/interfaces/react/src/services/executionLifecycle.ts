import type {ExecutionHandle, ExecutionService} from './ExecutionService.js';

export interface StopExecutionOptions {
    executionHandle?: ExecutionHandle | null;
    executionService?: ExecutionService | null;
    cleanup?: boolean;
    removeListeners?: boolean;
}

export async function stopExecution({
                                        executionHandle,
                                        executionService,
                                        cleanup = false,
                                        removeListeners = false,
                                    }: StopExecutionOptions): Promise<void> {
    let stopError: unknown;

    try {
        if (executionHandle) {
            await executionHandle.stop();
        } else if (executionService) {
            await executionService.stop();
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

