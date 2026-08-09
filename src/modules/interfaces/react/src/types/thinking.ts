export type ThinkingContext =
  | "reasoning"
  | "tool_preparation"
  | "tool_execution"
  | "waiting"
  | "startup"
  | "rate_limit";

export interface ThinkingStatus {
  active: boolean;
  context?: ThinkingContext;
  message?: string;
  startTime?: number;
  taskTitle?: string | null;
}
