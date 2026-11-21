"""Role-neutral principles for interpreting security evidence and future checks."""

SECURITY_INTERPRETATION = (
    "Keep observed facts, assumptions and unresolved prerequisites explicit. Identify expected legitimate "
    "behavior and the suspected security consequence. What observation would distinguish that consequence "
    "from legitimate behavior? Use a baseline, a controlled variation and the evidence needed to tell them "
    "apart. Successful execution or a different response establishes only what was observed. Establish "
    "relevant policy from documentation, configuration or independently known authorized and denied "
    "baselines. Unknown implementation details can become future checks when observed behavior motivates "
    "a conditional proposal. Observed client input and data flow can provide that basis without confirmed "
    "server handling. Preserve uncertainty until the distinguishing evidence is available. "
)
