import { jest, expect, describe, it, beforeEach, afterEach } from "@jest/globals";
import { OrchestratorClient, type OrchestratorCallbacks } from "./orchestratorClient";

// ---------------------------------------------------------------------------
// Mock WebSocket Implementation
// ---------------------------------------------------------------------------
class MockWebSocket {
    static instances: MockWebSocket[] = [];

    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;

    url: string;
    readyState: number = MockWebSocket.OPEN;
    send = jest.fn();
    close = jest.fn();

    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void | Promise<void>) | null = null;
    onerror: ((error: unknown) => void) | null = null;
    onclose: (() => void) | null = null;

    constructor(url: string) {
        this.url = url;
        MockWebSocket.instances.push(this);
    }

    // Helper for tests: simulate incoming server message
    emitMessage(data: object | string) {
        if (this.onmessage) {
            const dataStr = typeof data === "string" ? data : JSON.stringify(data);
            this.onmessage({ data: dataStr });
        }
    }

    // Helper for tests: simulate WebSocket error
    emitError(error: unknown) {
        if (this.onerror) {
            this.onerror(error);
        }
    }
}

// ---------------------------------------------------------------------------

describe("OrchestratorClient (WebSocket)", () => {
    let client: OrchestratorClient;
    let callbacks: OrchestratorCallbacks;

    const mockUrl = "wss://api.example.com/ws";
    const mockPoolId = "eu-west-2:00000000-0000-0000-0000-000000000000";
    const mockRegion = "eu-west-2";

    beforeEach(() => {
        jest.useFakeTimers();
        MockWebSocket.instances = [];

        // Replace global WebSocket with mock
        global.WebSocket = MockWebSocket as unknown as typeof WebSocket;

        client = new OrchestratorClient(mockUrl, mockPoolId, mockRegion);

        callbacks = {
            onChunk: jest.fn<(chunk: string) => void>(),
            onResponse: jest.fn<(response: string) => Promise<void> | void>().mockImplementation(() => {}),
            onSignal: jest.fn<(state: string, payload: unknown) => Promise<void> | void>().mockImplementation(() => {}),
            onComplete: jest.fn<() => Promise<void> | void>().mockImplementation(() => {}),
            onError: jest.fn<(error: unknown) => void>(),
        };
    });

    afterEach(() => {
        jest.restoreAllMocks();
        jest.useRealTimers();
    });

    it("should establish a WebSocket connection and send the payload when socket is OPEN", async () => {
        await client.sendMessage("hello", "thread-1234567890-1234567890-123456", callbacks);

        expect(MockWebSocket.instances.length).toBe(1);
        const ws = MockWebSocket.instances[0];

        expect(ws.url).toBe(mockUrl);
        expect(ws.send).toHaveBeenCalledWith(JSON.stringify({
            message: "hello",
            thread_id: "thread-1234567890-1234567890-123456",
            actor_id: "test"
        }));
    });

    it("should queue sending payload until socket fires onopen if initially CONNECTING", async () => {
        // Force socket state to CONNECTING
        const originalConstructor = global.WebSocket;

        const mockWsConstructor = jest.fn().mockImplementation((url: unknown) => {
            const ws = new MockWebSocket(url as string);
            ws.readyState = MockWebSocket.CONNECTING; // 0
            return ws;
        });

        // Re-attach the static constants so the client's if-statements still work!
        Object.assign(mockWsConstructor, {
            CONNECTING: 0,
            OPEN: 1,
            CLOSING: 2,
            CLOSED: 3
        });

        global.WebSocket = mockWsConstructor as unknown as typeof WebSocket;

        client = new OrchestratorClient(mockUrl, mockPoolId, mockRegion);
        await client.sendMessage("hello", "thread-1234567890-1234567890-123456", callbacks);

        const ws = MockWebSocket.instances[0];
        expect(ws.send).not.toHaveBeenCalled();

        // Simulate socket connection opening
        if (ws.onopen) ws.onopen();

        expect(ws.send).toHaveBeenCalledWith(JSON.stringify({
            message: "hello",
            thread_id: "thread-1234567890-1234567890-123456",
            actor_id: "test"
        }));

        global.WebSocket = originalConstructor;
    });
    it("should process streaming chunk frames and finalize upon receiving 'done' frame", async () => {
        await client.sendMessage("hello", "thread-1234567890-1234567890-123456", callbacks);
        const ws = MockWebSocket.instances[0];

        // Simulate incoming streaming tokens
        ws.emitMessage({ type: "chunk", text: "Hello " });
        ws.emitMessage({ type: "chunk", text: "from " });
        ws.emitMessage({ type: "chunk", text: "AI!" });

        expect(callbacks.onChunk).toHaveBeenCalledWith("Hello ");
        expect(callbacks.onChunk).toHaveBeenCalledWith("from ");
        expect(callbacks.onChunk).toHaveBeenCalledWith("AI!");

        // Send completion frame
        ws.emitMessage({ type: "done" });
        await Promise.resolve(); // Flush microtask queue for async callbacks

        expect(callbacks.onResponse).toHaveBeenCalledWith("Hello from AI!");
        expect(callbacks.onComplete).toHaveBeenCalled();
        expect(callbacks.onError).not.toHaveBeenCalled();
    });

    it("should handle state_change frame for CRM handoffs", async () => {
        await client.sendMessage("connect to live agent", "thread-1234567890-1234567890-123456", callbacks);
        const ws = MockWebSocket.instances[0];

        const handoffPayload = { target: "hmrc-tax-002" };
        ws.emitMessage({ type: "state_change", state: "HUMAN_CHAT", payload: handoffPayload });
        await Promise.resolve(); // Flush microtask queue

        expect(callbacks.onSignal).toHaveBeenCalledWith("HUMAN_CHAT", handoffPayload);
    });

    it("should handle human_message frames from CRM agents", async () => {
        await client.sendMessage("hello agent", "thread-1234567890-1234567890-123456", callbacks);
        const ws = MockWebSocket.instances[0];

        ws.emitMessage({ type: "human_message", text: "Hi, I am Dave from DWP." });

        expect(callbacks.onChunk).toHaveBeenCalledWith("Hi, I am Dave from DWP.");

        ws.emitMessage({ type: "done" });
        await Promise.resolve(); // Flush microtask queue

        expect(callbacks.onResponse).toHaveBeenCalledWith("Hi, I am Dave from DWP.");
    });

    it("should trigger fallback timeout if 'done' frame is missing", async () => {
        await client.sendMessage("hello", "thread-1234567890-1234567890-123456", callbacks);
        const ws = MockWebSocket.instances[0];

        ws.emitMessage({ type: "chunk", text: "Response without done frame" });

        expect(callbacks.onResponse).not.toHaveBeenCalled();

        // Fast-forward past the 2.5s debounce timeout
        jest.advanceTimersByTime(2600);
        await Promise.resolve(); // Flush microtask queue for the async timeout callback

        expect(callbacks.onResponse).toHaveBeenCalledWith("Response without done frame");
        expect(callbacks.onComplete).toHaveBeenCalled();
    });

    it("should call onError when WebSocket encounters an error", async () => {
        // Spy on console.error to keep the test runner output clean
        const consoleSpy = jest.spyOn(console, "error").mockImplementation(() => {});

        await client.sendMessage("hello", "thread-1234567890-1234567890-123456", callbacks);
        const ws = MockWebSocket.instances[0];

        const error = new Error("WebSocket connection error");
        ws.emitError(error);

        expect(callbacks.onError).toHaveBeenCalledWith(error);
        expect(callbacks.onResponse).not.toHaveBeenCalled();

        consoleSpy.mockRestore();
    });

    it("should handle malformed JSON frames gracefully without throwing", async () => {
        const consoleSpy = jest.spyOn(console, "error").mockImplementation(() => {});
        await client.sendMessage("hello", "thread-1234567890-1234567890-123456", callbacks);
        const ws = MockWebSocket.instances[0];

        // Emit raw string that is not valid JSON
        ws.emitMessage("invalid json frame");

        expect(consoleSpy).toHaveBeenCalledWith(
            "[WebSocket] Failed to parse frame. Raw data:",
            "invalid json frame"
        );
        expect(callbacks.onResponse).not.toHaveBeenCalled();

        consoleSpy.mockRestore();
    });
});
