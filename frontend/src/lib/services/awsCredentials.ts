/**
 * Fetches temporary AWS credentials from a Cognito Identity Pool configured
 * for "Unauthenticated Identities". These credentials are scoped (by the IAM
 * role attached to the pool) to only allow invoking the Orchestrator Lambda URL.
 *
 * Credentials are cached in-memory and refreshed automatically before expiry.
 */

import { CognitoIdentityClient, GetIdCommand, GetCredentialsForIdentityCommand } from "@aws-sdk/client-cognito-identity";

export interface AwsCredentials {
    accessKeyId: string;
    secretAccessKey: string;
    sessionToken: string;
    expiration: Date;
}

// In-memory cache to avoid fetching on every request
let cachedCredentials: AwsCredentials | null = null;

// Refresh 60 seconds before expiry to avoid race conditions
const REFRESH_BUFFER_MS = 60_000;

function isExpired(creds: AwsCredentials): boolean {
    return Date.now() >= creds.expiration.getTime() - REFRESH_BUFFER_MS;
}

/**
 * Returns cached temporary credentials, refreshing them if they are missing
 * or within the refresh buffer window.
 */
export async function getAwsCredentials(
    identityPoolId: string,
    region: string
): Promise<AwsCredentials> {
    if (cachedCredentials && !isExpired(cachedCredentials)) {
        return cachedCredentials;
    }

    const client = new CognitoIdentityClient({ region });

    // Step 1: Get an anonymous Cognito Identity ID for this user
    const { IdentityId } = await client.send(
        new GetIdCommand({ IdentityPoolId: identityPoolId })
    );

    if (!IdentityId) {
        throw new Error("Cognito GetId returned no IdentityId");
    }

    // Step 2: Exchange the Identity ID for temporary AWS credentials
    const { Credentials } = await client.send(
        new GetCredentialsForIdentityCommand({ IdentityId })
    );

    if (
        !Credentials?.AccessKeyId ||
        !Credentials?.SecretKey ||
        !Credentials?.SessionToken ||
        !Credentials?.Expiration
    ) {
        throw new Error("Cognito GetCredentialsForIdentity returned incomplete credentials");
    }

    cachedCredentials = {
        accessKeyId: Credentials.AccessKeyId,
        secretAccessKey: Credentials.SecretKey,
        sessionToken: Credentials.SessionToken,
        expiration: Credentials.Expiration
    };

    return cachedCredentials;
}
