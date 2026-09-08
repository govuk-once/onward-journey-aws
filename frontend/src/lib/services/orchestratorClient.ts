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
    private identityPoolId: string;
    private region: string;
    private ws: WebSocket | null = null;

    constructor(url: string, identityPoolId: string, region: string) {
        // TODO: these parameters are retained so Svelte component instantiation doesn't break.
        // They will potentially be useful when for Subtask 6 and implementation of
        // presigned WSS URLs or passing Cognito tokens during the socket connection phase.
        this.url = url;
        this.identityPoolId = identityPoolId;
        this.region = region;
    }

    async sendMessage(message: string, threadId: string, callbacks: OrchestratorCallbacks) {
        try {
            // 1. Initialize or reuse WebSocket connection
            if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
                this.ws = new WebSocket(this.url);
            }

            let fullText = "";
            let streamTimeout: ReturnType<typeof setTimeout>;

            // Helper to cleanly finalize a message
            const finalizeMessage = async () => {
                if (fullText.trim()) {
                    await callbacks.onResponse(fullText.trim());
                }
                await callbacks.onComplete();
                fullText = ""; // Reset buffer for the next message
            };

            // 2. Handle incoming JSON frames
            this.ws.onmessage = async (event) => {
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
                            await finalizeMessage();
                            break;

                        default:
                            console.warn("[WebSocket] Unknown frame type received:", frame.type);
                    }
                } catch {
                    console.error("[WebSocket] Failed to parse frame. Raw data:", event.data);
                }

                // Debounce Timer: Wait 2.5s for silence in case 'done' frame is dropped over the network
                clearTimeout(streamTimeout);
                streamTimeout = setTimeout(() => {
                    finalizeMessage();
                }, 2500);
            };

            // 3. Handle connection errors
            this.ws.onerror = (error) => {
                console.error("[WebSocket Error]", error);
                callbacks.onError(error);
            };

            // 4. Send the user message payload
            const payload = JSON.stringify({
                message,
                thread_id: threadId,
                actor_id: 'test'
            });

            // If the socket is still opening, queue the send. Otherwise, send immediately.
            if (this.ws.readyState === WebSocket.CONNECTING) {
                this.ws.onopen = () => {
                    console.log("[WebSocket] Connected successfully.");
                    this.ws!.send(payload);
                };
            } else if (this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(payload);
            } else {
                throw new Error("WebSocket is in a closing or closed state.");
            }

        } catch (error) {
            callbacks.onError(error);
        }
    }
}
