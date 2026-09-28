import { jest, expect, describe, it, beforeEach, afterEach } from "@jest/globals";
import { OrchestratorClient, type OrchestratorCallbacks } from "./orchestratorClient";

// ---------------------------------------------------------------------------
// Mock WebSocket Implementation
// ---------------------------------------------------------------------------

type MockCallback = (event: MessageEvent & Event) => void | Promise<void>;

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

    private listeners: Record<string, Array<{ callback: MockCallback; once: boolean }>> = {
        open: [],
        message: [],
        error: [],
        close: []
    };

    constructor(url: string) {
        this.url = url;
        MockWebSocket.instances.push(this);
    }

    addEventListener(event: string, callback: MockCallback, options?: { once?: boolean }) {
        if (!this.listeners[event]) this.listeners[event] = [];
        this.listeners[event].push({ callback, once: options?.once || false });
    }

    removeEventListener(event: string, callback: MockCallback) {
        if (!this.listeners[event]) return;
        this.listeners[event] = this.listeners[event].filter((l) => l.callback !== callback);
    }

    // Helper to fire events to attached listeners
    private dispatchEvent(event: string, payload?: unknown) {
        if (!this.listeners[event]) return;

        const currentListeners = [...this.listeners[event]];
        for (const listener of currentListeners) {
            listener.callback(payload as MessageEvent & Event);
            if (listener.once) {
                this.removeEventListener(event, listener.callback);
            }
        }
    }

    // Helper for tests: simulate incoming server message
    emitMessage(data: object | string) {
        const dataStr = typeof data === "string" ? data : JSON.stringify(data);
        // Dispatch an object matching the shape of a MessageEvent
        this.dispatchEvent("message", { data: dataStr });
    }

    // Helper for tests: simulate WebSocket error
    emitError(error: unknown) {
        this.dispatchEvent("error", error);
    }

    // Helper for tests: simulate successful connection
    emitOpen() {
        this.readyState = MockWebSocket.OPEN;
        this.dispatchEvent("open");
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

        // Re-attach the static constants so the client's if-statements still work
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
        ws.emitOpen();

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

    it("should NOT double-fire onComplete if a 'done' frame is received (prevents timer race condition)", async () => {
        await client.sendMessage("hello", "thread-1234567890-1234567890-123456", callbacks);
        const ws = MockWebSocket.instances[0];

        // Fire the done frame
        ws.emitMessage({ type: "done" });
        await Promise.resolve();

        // Verify it was called exactly once
        expect(callbacks.onComplete).toHaveBeenCalledTimes(1);

        // Fast-forward past the 2.5s debounce timeout window
        jest.advanceTimersByTime(3000);
        await Promise.resolve();

        // Verify it wasn't called a second time by the fallback timer
        expect(callbacks.onComplete).toHaveBeenCalledTimes(1);
    });

    it("should re-initialize the WebSocket if the current socket is in a CLOSING state", async () => {
        // 1st message creates the first socket
        await client.sendMessage("hello", "thread-1234567890", callbacks);
        expect(MockWebSocket.instances.length).toBe(1);

        const firstWs = MockWebSocket.instances[0];
        // Manually put the first socket into a CLOSING state
        firstWs.readyState = MockWebSocket.CLOSING;

        // 2nd message should detect CLOSING and create a new socket
        await client.sendMessage("hello again", "thread-1234567890", callbacks);

        expect(MockWebSocket.instances.length).toBe(2);
        const secondWs = MockWebSocket.instances[1];

        // Ensure the payload was sent down the NEW socket, not the old one
        expect(secondWs.send).toHaveBeenCalled();
    });

    it("should safely clean up event listeners to prevent cross-contamination on reused sockets", async () => {
        // Send first message
        await client.sendMessage("message 1", "thread-1", callbacks);
        const ws = MockWebSocket.instances[0];

        // Finish first message
        ws.emitMessage({ type: "chunk", text: "Response 1" });
        ws.emitMessage({ type: "done" });
        await Promise.resolve();

        // Send second message on same socket
        await client.sendMessage("message 2", "thread-1", callbacks);

        // Ensure new socket wasn't created unnecessarily
        expect(MockWebSocket.instances.length).toBe(1);

        // Finish second message
        ws.emitMessage({ type: "chunk", text: "Response 2" });
        ws.emitMessage({ type: "done" });
        await Promise.resolve();

        // If listeners not cleaned up, Response 2 would have triggered FIRST callback twice.
        // Checking exactly what was passed to onResponse proves they stayed isolated.
        expect(callbacks.onResponse).toHaveBeenCalledTimes(2);
        expect(callbacks.onResponse).toHaveBeenNthCalledWith(1, "Response 1");
        expect(callbacks.onResponse).toHaveBeenNthCalledWith(2, "Response 2");
    });
});
