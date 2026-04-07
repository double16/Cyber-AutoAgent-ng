import unittest

import pytest

from modules.handlers.report_generator import _sanitize_mermaid_diagrams


class TestMermaidSanitization(unittest.TestCase):
    def test_basic_node_sanitization(self):
        test_input = "```mermaid\ngraph TD\nA[Node with [brackets]] --> B(Node with (parens))\n```"
        output = _sanitize_mermaid_diagrams(test_input)
        self.assertIn('A["Node with [brackets]"]', output)
        self.assertIn('B("Node with (parens)")', output)

    @pytest.mark.skip("Not supported yet, need to handle nested deliminators")
    def test_complex_node_sanitization(self):
        test_input = "```mermaid\ngraph TD\nC((Double (rounded)))\nD{Node {with} braces}\nE>Node > with angle]\n```"
        output = _sanitize_mermaid_diagrams(test_input)
        self.assertIn('C(("Double (rounded)"))', output)
        self.assertIn('D{"Node {with} braces"}', output)
        self.assertIn('E>"Node > with angle"]', output)

    def test_edge_label_sanitization(self):
        test_input = "```mermaid\ngraph TD\nA -- label with [brackets] --> B\n```"
        output = _sanitize_mermaid_diagrams(test_input)
        self.assertIn('-- "label with [brackets]" -->', output)

    def test_sequence_diagram_sanitization(self):
        test_input = "```mermaid\nsequenceDiagram\nAlice->>Bob: Hello (world)!\n```"
        output = _sanitize_mermaid_diagrams(test_input)
        self.assertIn('Alice->>Bob: "Hello (world)!"', output)

    @pytest.mark.skip("Not supported yet, need to handle escaped pipes")
    def test_pipe_label_sanitization(self):
        test_input = "```mermaid\ngraph TD\nA|label with | pipe| --> B\n```"
        output = _sanitize_mermaid_diagrams(test_input)
        self.assertIn('|"label with | pipe|"|', output)

    def test_already_quoted_labels(self):
        test_input = '```mermaid\ngraph TD\nA["Already [quoted]"] --> B\n```'
        output = _sanitize_mermaid_diagrams(test_input)
        self.assertIn('A["Already [quoted]"]', output)
        self.assertNotIn('""', output)  # Should not have double quotes

    def test_multiple_diagrams(self):
        test_input = "```mermaid\ngraph TD\nA --> B\n```\n```mermaid\ngraph TD\nC --> D\n```"
        output = _sanitize_mermaid_diagrams(test_input)
        self.assertIn('A --> B', output)
        self.assertIn('C --> D', output)
        assert output.count('```mermaid\ngraph TD') == 2, "Should have two graph TD diagrams"

    def test_flow_chart_space_separated(self):
        test_input = """```mermaid
flowchart TD
    A[Reconnaissance – 120+ endpoints] --> B[Identify XSS endpoint (/vulnerabilities/xss_s/)]
    B --> C[Inject malicious JS via mtxMessage]
    C --> D[Steal session cookie (PHPSESSID)]
    D --> E[Authenticated actions as victim]
    E --> F[Potential admin/privileged operations]
    style A fill:#f9f,stroke:#333,stroke-width:2px
    style B fill:#bbf,stroke:#333,stroke-width:2px
    style C fill:#ff9,stroke:#333,stroke-width:2px
    style D fill:#f96,stroke:#333,stroke-width:2px
    style E fill:#9f9,stroke:#333,stroke-width:2px
    style F fill:#f66,stroke:#333,stroke-width:2px
```"""
        output = _sanitize_mermaid_diagrams(test_input)
        assert output.strip() == """```mermaid
flowchart TD
    A["Reconnaissance – 120+ endpoints"] --> B["Identify XSS endpoint (/vulnerabilities/xss_s/)"]
    B --> C["Inject malicious JS via mtxMessage"]
    C --> D["Steal session cookie (PHPSESSID)"]
    D --> E["Authenticated actions as victim"]
    E --> F["Potential admin/privileged operations"]
    style A fill:#f9f,stroke:#333,stroke-width:2px
    style B fill:#bbf,stroke:#333,stroke-width:2px
    style C fill:#ff9,stroke:#333,stroke-width:2px
    style D fill:#f96,stroke:#333,stroke-width:2px
    style E fill:#9f9,stroke:#333,stroke-width:2px
    style F fill:#f66,stroke:#333,stroke-width:2px
```
""".strip()

    def test_flow_chart_not_space_separated(self):
        test_input = """```mermaid
flowchart TD
    A[Reconnaissance – 120+ endpoints]-->B[Identify XSS endpoint (/vulnerabilities/xss_s/)]
    B-->C[Inject malicious JS via mtxMessage]
    C-->D[Steal session cookie (PHPSESSID)]
    D-->E[Authenticated actions as victim]
    E-->F[Potential admin/privileged operations]
    style A fill:#f9f,stroke:#333,stroke-width:2px
    style B fill:#bbf,stroke:#333,stroke-width:2px
    style C fill:#ff9,stroke:#333,stroke-width:2px
    style D fill:#f96,stroke:#333,stroke-width:2px
    style E fill:#9f9,stroke:#333,stroke-width:2px
    style F fill:#f66,stroke:#333,stroke-width:2px
```"""
        output = _sanitize_mermaid_diagrams(test_input)
        assert output.strip() == """```mermaid
flowchart TD
    A["Reconnaissance – 120+ endpoints"]-->B["Identify XSS endpoint (/vulnerabilities/xss_s/)"]
    B-->C["Inject malicious JS via mtxMessage"]
    C-->D["Steal session cookie (PHPSESSID)"]
    D-->E["Authenticated actions as victim"]
    E-->F["Potential admin/privileged operations"]
    style A fill:#f9f,stroke:#333,stroke-width:2px
    style B fill:#bbf,stroke:#333,stroke-width:2px
    style C fill:#ff9,stroke:#333,stroke-width:2px
    style D fill:#f96,stroke:#333,stroke-width:2px
    style E fill:#9f9,stroke:#333,stroke-width:2px
    style F fill:#f66,stroke:#333,stroke-width:2px
```
""".strip()

    def test_subgraph(self):
        test_input = """```mermaid
graph LR
    A["Anonymous"] -->|"Access"| B["Login Page (/login.php)"]
    B -->|"Successful Auth"| C["User Session"]
    C -->|"Elevated Privileges"| D["Admin Session"]
    D -->|"Service‑to‑Service"| E["Background Jobs / API"]
    subgraph Third‑Party
        F["External CDN / WAF"]
    end
    A -.-> F
    style A fill:#e3e,stroke:#333,stroke-width:2px
    style D fill:#f66,stroke:#333,stroke-width:2px
```"""
        output = _sanitize_mermaid_diagrams(test_input)
        assert output.strip() == """```mermaid
graph LR
    A["Anonymous"] -->|"Access"| B["Login Page (/login.php)"]
    B -->|"Successful Auth"| C["User Session"]
    C -->|"Elevated Privileges"| D["Admin Session"]
    D -->|"Service‑to‑Service"| E["Background Jobs / API"]
    subgraph "Third‑Party"
        F["External CDN / WAF"]
    end
    A -.-> F
    style A fill:#e3e,stroke:#333,stroke-width:2px
    style D fill:#f66,stroke:#333,stroke-width:2px
```
""".strip()

if __name__ == '__main__':
    unittest.main()
