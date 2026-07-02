import logging
import time
from opentelemetry import trace

logger = logging.getLogger("CyberAutoAgent")


def flush_traces(telemetry):
    """
    Flush OpenTelemetry traces before exiting the thread the agent is running in.
    """
    try:
        # Use the telemetry instance if available, otherwise use global tracer provider
        if telemetry and hasattr(telemetry, "tracer_provider"):
            tracer_provider = telemetry.tracer_provider
        else:
            tracer_provider = trace.get_tracer_provider()

        if hasattr(tracer_provider, "force_flush"):
            logger.debug("Flushing OpenTelemetry traces...")
            # Force flush with timeout to ensure traces are sent
            # This is critical for capturing all tool calls and swarm operations
            tracer_provider.force_flush(timeout_millis=10000)  # 10 second timeout
            # Short delay to ensure network transmission completes
            time.sleep(2)
            logger.debug("Traces flushed successfully")
    except Exception as e:
        logger.warning("Error flushing traces: %s", e)
