"""IDOR specialist - Analyze likely object identifier patterns and candidate ranges."""

import json
import logging
import os
from typing import Optional

from strands import Agent, tool
from strands_tools import editor
from modules.tools import shell

logger = logging.getLogger(__name__)


IDOR_METHODOLOGY = """<idor_specialist>
<role>IDOR analysis specialist - Identify likely object references and testable identifier ranges</role>

<mandate>
Help the agent reason about insecure direct object reference opportunities by:
1. Identifying object-reference-like parameters and values
2. Classifying identifier formats
3. Suggesting nearby identifiers or ranges worth testing
4. Explaining when identifiers appear enumerable vs non-enumerable
</mandate>

<identifier_types>
- integer
- uuid
- hex
- slug_with_numeric_component
- unknown
</identifier_types>

<analysis_rules>
- Numeric identifiers are often enumerable; suggest nearby values and small ranges
- UUIDs are less likely to be enumerable directly, but still note authorization risk
- Hex identifiers may be sequential; suggest nearby values when appropriate
- Slugs containing numbers may be partially enumerable by mutating the numeric part
- Prefer conservative, structured output
</analysis_rules>

<output_format>
Return JSON only:
{
  "identifier_assessment": [
    {
      "value": "observed identifier",
      "identifier_type": "integer | uuid | hex | slug_with_numeric_component | unknown",
      "enumerable": true,
      "candidate_values": ["41", "43", "44"],
      "notes": "Why these candidates make sense"
    }
  ],
  "overall_notes": "Summary of likely IDOR testing opportunities",
  "recommendation": "Specific next step"
}
</output_format>
</idor_specialist>"""


@tool
def idor_specialist(
    target_description: str,
    observed_identifiers: list[str],
    parameter_name: str = "id",
) -> dict:
    """Analyze observed identifiers and suggest candidate values/ranges for IDOR testing.

    Args:
        target_description: Description of the endpoint, route, or context being analyzed.
        observed_identifiers: List of identifiers observed in requests, responses, or URLs.
        parameter_name: Name of the parameter or field carrying the identifier.

    Returns:
        Structured response describing likely identifier types and candidate values.
    """
    agent_factory = getattr(idor_specialist, "agent_factory", None)
    assert agent_factory is not None

    specialist: Optional[Agent] = None
    try:
        tools = [editor, shell]

        operation_id = os.getenv("CYBER_OPERATION_ID") or "unknown"

        specialist = agent_factory(
            name=f"Cyber-idor_specialist {operation_id}",
            agent_type="idor_specialist",
            system_prompt=IDOR_METHODOLOGY,
            tools=tools,
        )

        task = f"""Analyze possible IDOR object references.

TARGET DESCRIPTION:
{target_description}

PARAMETER NAME:
{parameter_name}

OBSERVED IDENTIFIERS:
{json.dumps(observed_identifiers, indent=2)}

Classify identifier types, determine whether they appear enumerable,
suggest nearby candidate values or ranges where appropriate,
and return JSON only."""

        result = specialist(task)
        result_text = str(result)

        if "{" in result_text and "}" in result_text:
            json_start = result_text.find("{")
            json_end = result_text.rfind("}") + 1
            json_str = result_text[json_start:json_end]
            return json.loads(json_str)

        return {
            "identifier_assessment": [],
            "overall_notes": "Could not parse IDOR analysis results",
            "recommendation": "Manually review observed identifiers and specialist output",
        }

    except Exception as e:
        logger.error(f"IDOR specialist error: {e}")
        return {
            "identifier_assessment": [],
            "overall_notes": f"IDOR specialist error: {str(e)}",
            "recommendation": "Fix specialist configuration",
        }
    finally:
        if specialist is not None:
            try:
                specialist.cleanup()
            except Exception as e:
                logger.debug("Cleaning up idor_specialist agent", exc_info=e)