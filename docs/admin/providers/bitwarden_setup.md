# Bitwarden Secrets Manager

## Prerequisites

You must create a [Machine Account](https://bitwarden.com/help/machine-accounts/) in Bitwarden Secrets Manager, grant it read access to the project(s) containing the secrets Nautobot needs, and generate an [Access Token](https://bitwarden.com/help/access-tokens/) for it.

!!! note
    The machine account can only retrieve secrets in projects it has been granted access to.

## Configuration

No `PLUGINS_CONFIG` settings are required. The connection to Bitwarden is defined in Nautobot itself, using an [External Integration](https://docs.nautobot.com/projects/core/en/stable/user-guide/platform-functionality/externalintegration/) whose Secrets Group holds the access token. This allows multiple Bitwarden organizations or instances to be used side by side, each with its own External Integration.

### 1. Store the Access Token

Create a Secret holding the machine account access token, using any provider **other than** Bitwarden Secrets Manager (for example, the built-in *Environment Variable* or *Text File* providers, or another provider from this app).

!!! warning
    The access token cannot itself be stored in Bitwarden Secrets Manager, since it is needed to authenticate with Bitwarden in the first place.

### 2. Create a Secrets Group

Create a Secrets Group and assign the access token Secret to it with:

- **Access Type**: `HTTP(S)`
- **Secret Type**: `Token`

### 3. Create an External Integration

Create an External Integration with:

- **Remote URL**: The Bitwarden API URL.
    - Bitwarden cloud (US): `https://api.bitwarden.com`
    - Bitwarden cloud (EU): `https://api.bitwarden.eu`
    - Self-hosted: `https://<your-server>/api`
- **Secrets Group**: The Secrets Group created above.
- **Extra Config** (optional): A JSON object with any of the following keys:
    - `identity_url` - The Bitwarden Identity URL. When omitted, it is derived from the Remote URL by replacing a leading `api.` host label with `identity.` (cloud), or a trailing `/api` path with `/identity` (self-hosted). It must be set if the Remote URL matches neither pattern.
    - `state_file` - Path to a file where the SDK can cache its authenticated session between lookups, reducing the number of logins against Bitwarden. The file must be writable by the Nautobot process and should be protected like any other credential.

For example, for a self-hosted instance using a non-standard layout:

```json
{
    "identity_url": "https://vault.example.com/bitwarden-identity",
    "state_file": "/opt/nautobot/bws_state"
}
```

!!! note
    The *Verify SSL*, *CA File Path*, *Timeout*, *HTTP Method* and *Headers* fields of the External Integration are not used by this provider; the Bitwarden SDK manages its own HTTP connections.

## Usage

When creating a Secret with the Bitwarden Secrets Manager provider, set the following parameters:

- `external_integration` - The External Integration created above.
- `secret_id` - The ID (UUID) of the secret in Bitwarden Secrets Manager. The ID can be copied from the secret's menu in the Bitwarden web app, or listed with `bws secret list`.

The value of the secret is returned.
