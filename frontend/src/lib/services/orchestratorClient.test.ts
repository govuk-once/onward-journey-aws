import { jest, expect, describe, it, beforeEach, afterEach } from "@jest/globals";
import type { OrchestratorCallbacks } from "./orchestratorClient";

// ---------------------------------------------------------------------------
// ESM-compatible mocking via jest.unstable_mockModule
// With --experimental-vm-modules, we use dynamic import() AFTER mocks are set up.
// ---------------------------------------------------------------------------

// Persistent mock fetch delegated to by the AwsClient mock
const mockAwsFetch = jest.fn() as jest.MockedFunction<typeof fetch>;

// Mock aws4fetch before the module under test is imported
jest.unstable_mockModule("aws4fetch", () => ({
    AwsClient: jest.fn().mockImplementation(() => ({
        fetch: (...args: Parameters<typeof fetch>) => mockAwsFetch(...args)
    }))
}));

// Mock awsCredentials before the module under test is imported
jest.unstable_mockModule("./awsCredentials", () => ({
    getAwsCredentials: jest.fn<() => Promise<{
        accessKeyId: string;
        secretAccessKey: string;
        sessionToken: string;
        expiration: Date;
    }>>().mockResolvedValue({
        accessKeyId: "AKIAIOSFODNN7EXAMPLE",
        secretAccessKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        sessionToken: "AQoXnyc4lcK4w/mock-session-token",
        expiration: new Date(Date.now() + 3600_000)
    })
}));

// Dynamically import AFTER mocks are registered
const { OrchestratorClient } = await import("./orchestratorClient");

// ---------------------------------------------------------------------------

describe("OrchestratorClient", () => {
    let client: InstanceType<typeof OrchestratorClient>;
    let callbacks: OrchestratorCallbacks;

    const mockUrl = "https://api.example.com/orchestrator";
    const mockPoolId = "eu-west-2:00000000-0000-0000-0000-000000000000";
    const mockRegion = "eu-west-2";

    beforeEach(() => {
        mockAwsFetch.mockReset();

        client = new OrchestratorClient(mockUrl, mockPoolId, mockRegion);
        callbacks = {
            onResponse: jest.fn<(response: string) => Promise<void> | void>().mockImplementation(() => {}),
            onSignal: jest.fn<(signal: string, payload: unknown) => Promise<void> | void>().mockImplementation(() => {}),
            onComplete: jest.fn<() => Promise<void> | void>().mockImplementation(() => {}),
            onError: jest.fn<(error: unknown) => void>(),
        } as unknown as OrchestratorCallbacks;
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("should handle a successful JSON response", async () => {
        const mockApiResponse = { response: "Hello from the AI!" };

        mockAwsFetch.mockResolvedValueOnce({
            ok: true,
            headers: new Headers({ "content-type": "application/json" }),
            json: async () => mockApiResponse
        } as Response);

        await client.sendMessage("hello", "thread-123", callbacks);

        expect(mockAwsFetch).toHaveBeenCalledWith(mockUrl, expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ message: "hello", thread_id: "thread-123", actor_id: "test" })
        }));

        expect(callbacks.onResponse).toHaveBeenCalledWith("Hello from the AI!");
        expect(callbacks.onComplete).toHaveBeenCalled();
        expect(callbacks.onError).not.toHaveBeenCalled();
    });

    it("should handle a response with an embedded signal", async () => {
        const signalPayload = { action: "handoff", url: "wss://test" };
        const mockApiResponse = {
            response: `Connecting you now. SIGNAL: handoff ${JSON.stringify(signalPayload)}`
        };

        mockAwsFetch.mockResolvedValueOnce({
            ok: true,
            headers: new Headers({ "content-type": "application/json" }),
            json: async () => mockApiResponse
        } as Response);

        await client.sendMessage("help", "thread-123", callbacks);

        expect(callbacks.onSignal).toHaveBeenCalledWith("handoff", signalPayload);
        expect(callbacks.onResponse).toHaveBeenCalledWith("Connecting you now.");
        expect(callbacks.onComplete).toHaveBeenCalled();
    });

    it("should handle stringified body", async () => {
        const mockApiResponse = {
            body: JSON.stringify({ response: "Response from Lambda" })
        };

        mockAwsFetch.mockResolvedValueOnce({
            ok: true,
            headers: new Headers({ "content-type": "application/json" }),
            json: async () => mockApiResponse
        } as Response);

        await client.sendMessage("hello", "thread-123", callbacks);

        expect(callbacks.onResponse).toHaveBeenCalledWith("Response from Lambda");
    });

    it("should call onError when fetch fails", async () => {
        const error = new Error("Network failure");
        mockAwsFetch.mockRejectedValueOnce(error);

        await client.sendMessage("hello", "thread-123", callbacks);

        expect(callbacks.onError).toHaveBeenCalledWith(error);
        expect(callbacks.onResponse).not.toHaveBeenCalled();
    });

    it("should call onError when response is not ok", async () => {
        mockAwsFetch.mockResolvedValueOnce({
            ok: false,
            status: 500
        } as Response);

        await client.sendMessage("hello", "thread-123", callbacks);

        expect(callbacks.onError).toHaveBeenCalledWith(expect.objectContaining({
            message: expect.stringContaining("500")
        }));
    });
});
