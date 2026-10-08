"""Unit tests for the Bitwarden Secrets Manager Secrets Provider."""

import os
from unittest.mock import patch

from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import ExternalIntegration, Secret, SecretsGroup, SecretsGroupAssociation
from nautobot.extras.secrets import exceptions

from nautobot_secrets_providers.providers import BitwardenSecretsManagerSecretsProvider
from nautobot_secrets_providers.providers.bitwarden import derive_identity_url, external_integration_choices
from nautobot_secrets_providers.tests.test_providers import SecretsProviderTestCase


@patch.dict(os.environ, {"BWS_ACCESS_TOKEN": "0.machine-account-token"})
class BitwardenSecretsManagerSecretsProviderTestCase(SecretsProviderTestCase):
    """Tests for BitwardenSecretsManagerSecretsProvider."""

    provider = BitwardenSecretsManagerSecretsProvider

    def setUp(self):
        super().setUp()

        self.token_secret = Secret.objects.create(
            name="bitwarden-access-token",
            provider="environment-variable",
            parameters={"variable": "BWS_ACCESS_TOKEN"},
        )
        self.secrets_group = SecretsGroup.objects.create(name="Bitwarden")
        SecretsGroupAssociation.objects.create(
            secrets_group=self.secrets_group,
            secret=self.token_secret,
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_TOKEN,
        )
        self.integration = ExternalIntegration.objects.create(
            name="Bitwarden Cloud",
            remote_url="https://api.bitwarden.com",
            secrets_group=self.secrets_group,
        )

        self.secret_id = "4f6c1f1e-9a3b-4c2d-8e7f-0a1b2c3d4e5f"
        self.secret = Secret.objects.create(
            name="hello-bitwarden",
            provider=self.provider.slug,
            parameters={"external_integration": self.integration.name, "secret_id": self.secret_id},
        )

    @patch("nautobot_secrets_providers.providers.bitwarden.BitwardenClient")
    def test_retrieve_success(self, mock_client_class):
        """Retrieve a secret successfully using the access token from the Secrets Group."""
        mock_client = mock_client_class.return_value
        mock_client.secrets.return_value.get.return_value.data.value = "world"

        response = self.provider.get_value_for_secret(self.secret)

        self.assertEqual("world", response)
        mock_client.auth.return_value.login_access_token.assert_called_once_with("0.machine-account-token", None)
        mock_client.secrets.return_value.get.assert_called_once_with(self.secret_id)
        client_settings = mock_client_class.call_args.args[0]
        self.assertEqual(client_settings.api_url, "https://api.bitwarden.com")
        self.assertEqual(client_settings.identity_url, "https://identity.bitwarden.com")

    @patch("nautobot_secrets_providers.providers.bitwarden.BitwardenClient")
    def test_retrieve_extra_config(self, mock_client_class):
        """Use the identity URL and state file from the External Integration's extra config."""
        mock_client = mock_client_class.return_value
        mock_client.secrets.return_value.get.return_value.data.value = "world"
        self.integration.remote_url = "https://vault.example.com/bitwarden-api"
        self.integration.extra_config = {
            "identity_url": "https://vault.example.com/bitwarden-identity",
            "state_file": "/tmp/bws_state",  # noqa: S108
        }
        self.integration.validated_save()

        self.provider.get_value_for_secret(self.secret)

        client_settings = mock_client_class.call_args.args[0]
        self.assertEqual(client_settings.api_url, "https://vault.example.com/bitwarden-api")
        self.assertEqual(client_settings.identity_url, "https://vault.example.com/bitwarden-identity")
        mock_client.auth.return_value.login_access_token.assert_called_once_with(
            "0.machine-account-token",
            "/tmp/bws_state",  # noqa: S108
        )

    def test_identity_url_not_derivable(self):
        """Raise an error when the identity URL can't be derived and isn't configured."""
        self.integration.remote_url = "https://vault.example.com/bitwarden-api"
        self.integration.validated_save()

        with self.assertRaises(exceptions.SecretProviderError) as err:
            self.provider.get_value_for_secret(self.secret)
        self.assertIn("identity_url", err.exception.message)

    def test_derive_identity_url(self):
        """Derive the identity URL for Bitwarden cloud and self-hosted API URLs."""
        self.assertEqual(derive_identity_url("https://api.bitwarden.com"), "https://identity.bitwarden.com")
        self.assertEqual(derive_identity_url("https://api.bitwarden.eu/"), "https://identity.bitwarden.eu")
        self.assertEqual(derive_identity_url("https://vault.example.com/api"), "https://vault.example.com/identity")
        self.assertIsNone(derive_identity_url("https://vault.example.com"))

    def test_external_integration_does_not_exist(self):
        """Raise an error when the referenced External Integration doesn't exist."""
        self.secret.parameters["external_integration"] = "missing"
        self.secret.validated_save()

        with self.assertRaises(exceptions.SecretParametersError):
            self.provider.get_value_for_secret(self.secret)

    def test_no_secrets_group(self):
        """Raise an error when the External Integration has no Secrets Group."""
        self.integration.secrets_group = None
        self.integration.validated_save()

        with self.assertRaises(exceptions.SecretProviderError) as err:
            self.provider.get_value_for_secret(self.secret)
        self.assertIn("no Secrets Group", err.exception.message)

    def test_no_token_in_secrets_group(self):
        """Raise an error when the Secrets Group has no HTTP(S) Token secret."""
        SecretsGroupAssociation.objects.filter(secrets_group=self.secrets_group).delete()

        with self.assertRaises(exceptions.SecretProviderError) as err:
            self.provider.get_value_for_secret(self.secret)
        self.assertIn("HTTP(S) Token", err.exception.message)

    def test_token_secret_recursion(self):
        """Refuse an access token secret that is itself stored in Bitwarden."""
        self.token_secret.provider = self.provider.slug
        self.token_secret.parameters = {"external_integration": self.integration.name, "secret_id": self.secret_id}
        self.token_secret.validated_save()

        with self.assertRaises(exceptions.SecretProviderError) as err:
            self.provider.get_value_for_secret(self.secret)
        self.assertIn("cannot itself use", err.exception.message)

    def test_retrieve_invalid_parameters(self):
        """Raise an error when a parameter is missing or the secret_id is not a UUID."""
        missing = Secret.objects.create(
            name="bitwarden-missing",
            provider=self.provider.slug,
            parameters={"secret_id": self.secret_id},
        )
        invalid = Secret.objects.create(
            name="bitwarden-invalid",
            provider=self.provider.slug,
            parameters={"external_integration": self.integration.name, "secret_id": "not-a-uuid"},
        )
        for secret in (missing, invalid):
            with self.assertRaises(exceptions.SecretParametersError):
                self.provider.get_value_for_secret(secret)

    @patch("nautobot_secrets_providers.providers.bitwarden.BitwardenClient")
    def test_login_failure(self, mock_client_class):
        """Raise a provider error when authentication fails."""
        mock_client_class.return_value.auth.return_value.login_access_token.side_effect = Exception("Invalid token")

        with self.assertRaises(exceptions.SecretProviderError) as err:
            self.provider.get_value_for_secret(self.secret)
        self.assertIn("Invalid token", err.exception.message)

    @patch("nautobot_secrets_providers.providers.bitwarden.BitwardenClient")
    def test_retrieve_does_not_exist(self, mock_client_class):
        """Raise a not found error when the secret cannot be retrieved."""
        mock_client_class.return_value.secrets.return_value.get.side_effect = Exception("404 Not Found")

        with self.assertRaises(exceptions.SecretValueNotFoundError) as err:
            self.provider.get_value_for_secret(self.secret)
        self.assertIn("404 Not Found", err.exception.message)

    def test_external_integration_choices(self):
        """List the External Integrations by name."""
        ExternalIntegration.objects.create(name="Another Integration", remote_url="https://example.com")
        self.assertEqual(
            external_integration_choices(),
            [("Another Integration", "Another Integration"), ("Bitwarden Cloud", "Bitwarden Cloud")],
        )
