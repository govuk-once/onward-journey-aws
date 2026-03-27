import { OrchestratorClient, type OrchestratorCallbacks } from "./orchestratorClient";
import { jest, expect } from "@jest/globals";

describe("OrchestratorClient", () => {
    let client: OrchestratorClient;
    let callbacks: OrchestratorCallbacks;
    const mockUrl = "https://api.example.com/orchestrator";

    beforeEach(() => {
        client = new OrchestratorClient(mockUrl);
        callbacks = {
            onResponse: jest.fn<(response: string) => Promise<void> | void>().mockImplementation(() => {}),
            onSignal: jest.fn<(signal: string, payload: unknown) => Promise<void> | void>().mockImplementation(() => {}),
            onComplete: jest.fn<() => Promise<void> | void>().mockImplementation(() => {}),
            onError: jest.fn<(error: unknown) => void>(),
        } as unknown as OrchestratorCallbacks;

        global.fetch = jest.fn() as unknown as typeof fetch;
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("should handle a successful JSON response", async () => {
        const mockApiResponse = {
            response: "Hello from the AI!"
        };

        const mockFetch = global.fetch as jest.MockedFunction<typeof fetch>;
        mockFetch.mockResolvedValueOnce({
            ok: true,
            headers: new Headers({ "content-type": "application/json" }),
            json: async () => mockApiResponse
        } as Response);

        await client.sendMessage("hello", "thread-123", callbacks);

        expect(global.fetch).toHaveBeenCalledWith(mockUrl, expect.objectContaining({
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

        const mockFetch = global.fetch as jest.MockedFunction<typeof fetch>;
        mockFetch.mockResolvedValueOnce({
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

        const mockFetch = global.fetch as jest.MockedFunction<typeof fetch>;
        mockFetch.mockResolvedValueOnce({
            ok: true,
            headers: new Headers({ "content-type": "application/json" }),
            json: async () => mockApiResponse
        } as Response);

        await client.sendMessage("hello", "thread-123", callbacks);

        expect(callbacks.onResponse).toHaveBeenCalledWith("Response from Lambda");
    });

    it("should call onError when fetch fails", async () => {
        const error = new Error("Network failure");
        const mockFetch = global.fetch as jest.MockedFunction<typeof fetch>;
        mockFetch.mockRejectedValueOnce(error);

        await client.sendMessage("hello", "thread-123", callbacks);

        expect(callbacks.onError).toHaveBeenCalledWith(error);
        expect(callbacks.onResponse).not.toHaveBeenCalled();
    });

    it("should call onError when response is not ok", async () => {
        const mockFetch = global.fetch as jest.MockedFunction<typeof fetch>;
        mockFetch.mockResolvedValueOnce({
            ok: false,
            status: 500
        } as Response);

        await client.sendMessage("hello", "thread-123", callbacks);

        expect(callbacks.onError).toHaveBeenCalledWith(expect.objectContaining({
            message: expect.stringContaining("500")
        }));
    });
});
