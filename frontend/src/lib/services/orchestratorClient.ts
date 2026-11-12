export interface OrchestratorMessage {
    text: string;
    isSignal: boolean;
    signalPayload?: unknown;
}

export interface OrchestratorCallbacks {
    onChunk?: (chunk: string) => void; // Real-time streaming chunks
    onResponse: (response: string) => Promise<void> | void; // Final accumulated text
    onSignal: (state: string, payload: unknown) => Promise<void> | void; // CRM Handoffs
    onComplete: () => Promise<void> | void;
    onError: (error: unknown) => void;
}

export class OrchestratorClient {
    private url: string;
    private authToken: string;
    private ws: WebSocket | null = null;

    constructor(url: string, authToken: string) {
        if (!url) throw new Error("Configuration error: url is missing.");
        if (!authToken) {
            throw new Error("Configuration error: PUBLIC_WEBSOCKET_AUTH_TOKEN is missing.");
        }

        this.url = url;
        this.authToken = authToken;
    }

    async sendMessage(message: string, threadId: string, callbacks: OrchestratorCallbacks) {
        try {
            // 1. Initialize or reuse WebSocket connection (Handle both CLOSED and CLOSING states)
            if (!this.ws || this.ws.readyState === WebSocket.CLOSED || this.ws.readyState === WebSocket.CLOSING) {
                const socketUrl = new URL(this.url);
                socketUrl.searchParams.set("auth_token", this.authToken);
                this.ws = new WebSocket(socketUrl.toString());
            }

            let fullText = "";
            let streamTimeout: ReturnType<typeof setTimeout>;

            // Helper to cleanly finalize a message
            const finalizeMessage = async (isError = false) => {
                if (!isError) {
                    if (fullText.trim()) {
                        await callbacks.onResponse(fullText.trim());
                    }
                    await callbacks.onComplete();
                }

                fullText = ""; // Reset buffer for the next message

                // --- CLEANUP: Remove event listeners to prevent memory leaks on reused sockets ---
                if (this.ws) {
                    this.ws.removeEventListener("message", messageHandler);
                    this.ws.removeEventListener("error", errorHandler);
                    this.ws.removeEventListener("close", closeHandler);
                }
            };

            // 2. Handle incoming JSON frames
            const messageHandler = async (event: MessageEvent) => {
                try {
                    const frame = JSON.parse(event.data);

                    switch (frame.type) {
                        case "chunk":
                            // Standard AI typing
                            fullText += frame.text;
                            if (callbacks.onChunk) callbacks.onChunk(frame.text);
                            break;

                        case "state_change":
                            // Backend signaled a CRM handoff (e.g., state = HUMAN_CHAT)
                            await callbacks.onSignal(frame.state, frame.payload);
                            break;

                        case "human_message":
                            // TODO: when CRM agents reply (human chat)
                            fullText += frame.text;
                            if (callbacks.onChunk) callbacks.onChunk(frame.text);
                            break;

                        case "done":
                            // AI has finished generating this turn
                            clearTimeout(streamTimeout);
                            await finalizeMessage(false); // Success
                            return; // Stop execution here so no new debounce timer is set

                        default:
                            console.warn("[WebSocket] Unknown frame type received:", frame.type);
                    }
                } catch {
                    console.error("[WebSocket] Failed to parse frame. Raw data:", event.data);
                }

                // Debounce Timer: Wait 30s for silence in case 'done' frame is dropped over the network
                clearTimeout(streamTimeout);
                streamTimeout = setTimeout(() => {
                    finalizeMessage(false); // Assume done if network goes quiet
                }, 30000);
            };

            this.ws.addEventListener("message", messageHandler);

            // 3. Handle connection errors
            const errorHandler = () => {
                // Do not log error object to avoid leaking event.target.url
                console.error("[WebSocket Error] Connection failed.");
                callbacks.onError(new Error("WebSocket connection failed."));
                clearTimeout(streamTimeout);
                finalizeMessage(true);
            };

            // Catch sudden closures (like API Gateway 401/403 token rejections)
            const closeHandler = (event: CloseEvent) => {
                // If it closed abnormally or wasn't cleanly closed
                if (event.code !== 1000) {
                    // Log code and reason, but NOT raw event object
                    console.warn(`[WebSocket Closed] Code: ${event.code}, Reason: ${event.reason || 'None'}`);
                    callbacks.onError(new Error(`WebSocket closed unexpectedly (Code: ${event.code})`));
                    clearTimeout(streamTimeout);
                    finalizeMessage(true);
                }
            };

            this.ws.addEventListener("error", errorHandler);
            this.ws.addEventListener("close", closeHandler);

            // 4. Send the user message payload
            const payload = JSON.stringify({
                message,
                thread_id: threadId,
            });

            // If the socket is still opening, queue the send. Otherwise, send immediately.
            if (this.ws.readyState === WebSocket.CONNECTING) {
                // Use { once: true } so this listener automatically deletes itself after firing
                this.ws.addEventListener("open", () => {
                    console.log("[WebSocket] Connected successfully.");
                    this.ws!.send(payload);
                }, { once: true });
            } else if (this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(payload);
            } else {
                throw new Error("WebSocket is in a closing or closed state.");
            }

        } catch (error) {
            let safeErrorMessage = "An unexpected error occurred.";
            if (error instanceof Error) {
                // Redact token if it happens to be in the error message
                safeErrorMessage = error.message.replaceAll(this.authToken, "***REDACTED***");
            }

            callbacks.onError(new Error(safeErrorMessage));
        }
    }
}
