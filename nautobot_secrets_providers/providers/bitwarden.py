"""Secrets Provider for Bitwarden Secrets Manager."""

import uuid
from urllib.parse import urlsplit, urlunsplit

from django import forms

try:
    from bitwarden_sdk import BitwardenClient, ClientSettings, DeviceType
except ImportError:
    BitwardenClient = None

from nautobot.core.forms import BootstrapMixin
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import ExternalIntegration, SecretsGroupAssociation
from nautobot.extras.secrets import SecretsProvider, exceptions

from nautobot_secrets_providers import __version__

__all__ = ("BitwardenSecretsManagerSecretsProvider",)


def external_integration_choices():
    """Generate choices for the external integration form field."""
    return [(name, name) for name in ExternalIntegration.objects.order_by("name").values_list("name", flat=True)]


def derive_identity_url(api_url):
    """Derive the Bitwarden Identity URL from the API URL.

    Bitwarden cloud uses separate hosts (`api.bitwarden.com` / `identity.bitwarden.com`),
    while self-hosted instances use paths on a single host (`/api` / `/identity`).

    Returns:
        (str): The derived Identity URL, or None if it cannot be derived.
    """
    parts = urlsplit(api_url.rstrip("/"))
    if parts.hostname and parts.hostname.startswith("api."):
        return urlunsplit(parts._replace(netloc=parts.netloc.replace("api.", "identity.", 1)))
    if parts.path.endswith("/api"):
        return urlunsplit(parts._replace(path=parts.path[: -len("/api")] + "/identity"))
    return None


class BitwardenSecretsManagerSecretsProvider(SecretsProvider):
    """A secrets provider for Bitwarden Secrets Manager."""

    slug = "bitwarden-secrets-manager"
    name = "Bitwarden Secrets Manager"
    is_available = BitwardenClient is not None

    # pylint: disable-next=nb-incorrect-base-class
    class ParametersForm(BootstrapMixin, forms.Form):
        """Required parameters for Bitwarden Secrets Manager."""

        external_integration = forms.ChoiceField(
            required=True,
            choices=external_integration_choices,
            help_text="External Integration defining the Bitwarden API URL and the Secrets Group holding the access token.",
        )
        secret_id = forms.CharField(
            required=True,
            help_text="The ID (UUID) of the secret in Bitwarden Secrets Manager.",
        )

    @classmethod
    def get_access_token(cls, secret, integration, obj=None):
        """Return the machine account access token from the External Integration's Secrets Group."""
        if integration.secrets_group is None:
            raise exceptions.SecretProviderError(
                secret, cls, f"External Integration {integration.name!r} has no Secrets Group configured!"
            )
        try:
            token_secret = SecretsGroupAssociation.objects.get(
                secrets_group=integration.secrets_group,
                access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
                secret_type=SecretsGroupSecretTypeChoices.TYPE_TOKEN,
            ).secret
        except SecretsGroupAssociation.DoesNotExist as err:
            msg = f"Secrets Group {integration.secrets_group.name!r} has no HTTP(S) Token secret for the access token!"
            raise exceptions.SecretProviderError(secret, cls, msg) from err

        # Guard against infinite recursion when the access token is itself stored in Bitwarden.
        if token_secret.provider == cls.slug:
            msg = f"The access token secret {token_secret.name!r} cannot itself use the {cls.name} provider!"
            raise exceptions.SecretProviderError(secret, cls, msg)

        return token_secret.get_value(obj=obj)

    @classmethod
    def get_client(cls, secret, integration, obj=None):
        """Return a Bitwarden client authenticated with the External Integration's access token."""
        extra_config = integration.extra_config or {}
        api_url = integration.remote_url.rstrip("/")
        identity_url = extra_config.get("identity_url") or derive_identity_url(api_url)
        if not identity_url:
            msg = (
                f"Could not derive the Bitwarden Identity URL from {api_url!r}; "
                "set 'identity_url' in the External Integration's extra config."
            )
            raise exceptions.SecretProviderError(secret, cls, msg)

        client = BitwardenClient(
            ClientSettings(
                api_url=api_url,
                identity_url=identity_url,
                device_type=DeviceType.SDK,
                user_agent=f"nautobot-secrets-providers/{__version__}",
            )
        )
        access_token = cls.get_access_token(secret, integration, obj=obj)
        try:
            client.auth().login_access_token(access_token, extra_config.get("state_file"))
        except Exception as err:
            raise exceptions.SecretProviderError(secret, cls, f"Failed to authenticate with Bitwarden: {err}") from err
        return client

    @classmethod
    def get_value_for_secret(cls, secret, obj=None, **kwargs):
        """Return the value of the secret from Bitwarden Secrets Manager."""
        parameters = secret.rendered_parameters(obj=obj)
        try:
            integration_name = parameters["external_integration"]
            secret_id = str(uuid.UUID(str(parameters["secret_id"])))
        except KeyError as err:
            msg = f"The secret parameter could not be retrieved for field {err}"
            raise exceptions.SecretParametersError(secret, cls, msg) from err
        except ValueError as err:
            msg = f"The secret_id {parameters['secret_id']!r} is not a valid UUID"
            raise exceptions.SecretParametersError(secret, cls, msg) from err

        try:
            integration = ExternalIntegration.objects.get(name=integration_name)
        except ExternalIntegration.DoesNotExist as err:
            msg = f"External Integration {integration_name!r} does not exist"
            raise exceptions.SecretParametersError(secret, cls, msg) from err

        client = cls.get_client(secret, integration, obj=obj)
        try:
            response = client.secrets().get(secret_id)
        except Exception as err:
            raise exceptions.SecretValueNotFoundError(secret, cls, str(err)) from err

        return response.data.value
