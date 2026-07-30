<module_report_configuration>
Module: CTF Challenge Assessment
Focus: Flag capture status, exploitation chain, technical evidence, reproduction steps
</module_report_configuration>

<domain_lens>
DOMAIN_LENS:
overview: Truthful CTF report based ONLY on actual evidence from memory and artifacts. Focus on the success or failure of flag capture and the technical validity of the exploitation chain
analysis: Analyze findings based on the technical path to the flag. Verify ground truth (memory, files) before claiming success. Prioritize techniques that led to successful state transitions or budget-efficient progress
immediate: If flag was captured, extract and document the exact value and artifact reference. If not captured, identify the specific blocking factor and recommended pivot strategies within the remaining challenge time
short_term: Document the full exploitation flow with a focus on reproducibility. Reference exact artifact paths instead of full payloads. Summarize failed attempts to guide future strategy
long_term: Develop technique patterns for similar challenges, create automated detectors for success-state transitions, and optimize tooling for future CTF runs
framework: CTF Assessment Methodology, Technical Proof-of-Concept
</domain_lens>

<validation_reporting_policy>
Vulnerability validation and objective validation are independent. Count only verified vulnerability records as
security findings. Report confirmed, rejected, and unresolved flag candidates in the objective-validation section; an
invalid flag must not downgrade a verified vulnerability, and a verified vulnerability must not be described as
successful flag capture.
</validation_reporting_policy>

<audience_adaptation>
CTF reports serve specialized evaluators:
- **Technical Reviewers**: Specific exploitation techniques, PoC reproducibility, artifact evidence
- **Strategy Leads**: Effectiveness of different approaches, tool performance, resource allocation
- **Future Teams**: Lessons learned, reusable technique patterns, common pitfalls
</audience_adaptation>
