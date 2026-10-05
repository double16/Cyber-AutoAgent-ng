import base64
import json

import pytest

from modules.tools.auth_protocol_evidence import authentication_indicators


def observe(body, headers=None, status_code=200):
    return authentication_indicators(body, headers or {}, "https://target.test/path", status_code)


@pytest.mark.parametrize("body", ["OAuth SAML session csrf", "<html>SPA</html>", '{"keys": []}',
                                  '{"keys": [{"kty": "RSA"}]}', '{"issuer": "issuer"}',
                                  '<metadata xmlns="other"/>', '<EntityDescriptor entityID="x"/>'])
def test_plain_content_and_malformed_documents_do_not_prove_protocol(body):
    assert observe(body) == []


def test_oidc_requires_complete_structural_urls():
    document = {key: f"https://identity.test/{key}" for key in
                ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")}
    result = observe(json.dumps(document))
    assert result[0]["indicator"] == "oidc_discovery"
    assert result[0]["supports_mechanism"] is True
    assert result[0]["response_evidence"]["body_sha256"]
    document["issuer"] = "http://"
    assert observe(json.dumps(document)) == []


@pytest.mark.parametrize("key", [{"kty": "RSA", "n": "n", "e": "e"},
                                 {"kty": "EC", "crv": "P-256", "x": "x", "y": "y"},
                                 {"kty": "OKP", "crv": "Ed25519", "x": "x"}])
def test_jwks_requires_public_key_material(key):
    assert observe(json.dumps({"keys": [key]}))[0]["indicator"] == "jwks_document"


def test_saml_metadata_requires_identity_provider_service():
    xml = ('<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="id">'
           '<IDPSSODescriptor><SingleSignOnService Binding="binding" Location="https://idp.test/sso"/>'
           '</IDPSSODescriptor></EntityDescriptor>')
    assert observe(xml)[0]["indicator"] == "saml_metadata"
    assert observe(xml.replace('Location="https://idp.test/sso"', "")) == []
    assert observe(f'<EntitiesDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata">{xml}</EntitiesDescriptor>')


def test_saml_message_requires_namespace_id_and_version():
    xml = '<p:Response xmlns:p="urn:oasis:names:tc:SAML:2.0:protocol" ID="id" Version="2.0"/>'
    assert observe(xml)[0]["indicator"] == "saml_message"
    assert observe(xml.replace('Version="2.0"', 'Version="1.0"')) == []


def jwt(header, payload):
    parts = [base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=") for value in (header, payload)]
    return ".".join([*parts, "signature"])


def test_jwt_structure_does_not_publish_token_value():
    token = jwt({"alg": "HS256"}, {"sub": "subject"})
    result = observe(token + " " + token)
    assert len(result) == 1 and result[0]["indicator"] == "jwt_structure"
    assert token not in json.dumps(result)
    assert observe(jwt({}, {"sub": "s"})) == []
    assert observe("eyJbroken.invalid.signature") == []


def test_forms_cookies_and_redirects_have_separate_semantics():
    assert observe('<input type="password">') == []
    assert observe('<form></form><input type="password">') == []
    indicators = observe('<form><input type="PASSWORD"></form>', {"Set-Cookie": "tracking=value"})
    assert {item["indicator"] for item in indicators} == {"password_form", "cookie_set"}
    assert not any(item["supports_mechanism"] for item in indicators)
    redirect = {"Location": "https://idp.test/authorize?client_id=c&redirect_uri=https://target.test&response_type=code"}
    assert observe("", redirect, 302)[0]["indicator"] == "authorization_redirect"
    assert observe("", redirect, 200) == []
