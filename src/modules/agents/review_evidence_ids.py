"""Bind model-facing evidence IDs to exact invocation-local inspection receipts."""

from copy import deepcopy

from pydantic import create_model, model_validator


def bind_evidence_ids(output_model, journal):
    """Refresh selectable evidence at schema generation, after investigation has completed."""
    references = {}

    def refresh():
        references.clear()
        references.update({
            f"e{index}": reference
            for index, (reference, availability) in enumerate(journal.inspection_availability().items(), 1)
            if availability != "unavailable"
        })

    refresh()

    def expand(cls, value):
        def visit(node):
            if isinstance(node, dict):
                return {
                    key: [references.get(item, item) for item in child]
                    if isinstance(key, str) and key.lower() == "inspection_refs" and isinstance(child, list)
                    else visit(child) for key, child in node.items()
                }
            if isinstance(node, list):
                return [visit(child) for child in node]
            return node
        return visit(value)

    model = create_model(
        output_model.__name__, __base__=output_model,
        __validators__={"expand_evidence_ids": model_validator(mode="before")(expand)},
    )

    def schema(cls, *args, **kwargs):
        refresh()
        result = deepcopy(output_model.model_json_schema(*args, **kwargs))

        def visit(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    if key == "inspection_refs" and isinstance(child, dict):
                        child["items"] = {
                            "type": "string", **({"enum": list(references)} if references else {}),
                            "description": "Select inspection evidence IDs: " + "; ".join(
                                f"{identifier} = {reference}" for identifier, reference in references.items()
                            ),
                        }
                        if not references:
                            child["maxItems"] = 0
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
        visit(result)
        return result

    model.model_json_schema = classmethod(schema)
    return model
