export const formatAutoRunTerminationEvent = (event: any): string | null => {
  if (event?.type !== 'termination_reason') return null;

  const reason = typeof event.reason === 'string' && event.reason.trim()
    ? event.reason.trim()
    : 'unknown';
  const message = typeof event.message === 'string' && event.message.trim()
    ? event.message
    : 'Operation terminated.';

  return `🛑 Termination (${reason}): ${message}`;
};
