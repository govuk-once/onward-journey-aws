export interface OrchestratorMessage {
    text: string;
    isSignal: boolean;
    signalPayload?: unknown;
}

export interface OrchestratorCallbacks {
    onResponse: (response: string) => Promise<void> | void;
    onSignal: (signal: string, payload: unknown) => Promise<void> | void;
    onComplete: () => Promise<void> | void;
    onError: (error: unknown) => void;
}

export class OrchestratorClient {
    private url: string;

    constructor(url: string) {
        this.url = url;
    }

    async sendMessage(message: string, threadId: string, callbacks: OrchestratorCallbacks) {
        try {
            const response = await fetch(this.url, {
                method: 'POST',
                credentials: "include",
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message, thread_id: threadId, actor_id: 'test' })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            // Handle JSON responses
            const contentType = response.headers.get("content-type");
            if (contentType?.includes("application/json")) {
                const data = await response.json();
                console.log(`[OrchestratorClient] Raw JSON response (Thread: ${threadId}):`, data);
                let responseText = data.response;

                // Handle stringified body from Lambda Proxy
                if (!responseText && typeof data.body === "string") {
                    try {
                        const parsedBody = JSON.parse(data.body);
                        responseText = parsedBody.response;
                    } catch (_e) {
                        console.error("Failed to parse response body as JSON", _e);
                    }
                }

                if (responseText) {
                    // Extract SIGNAL if present: SIGNAL: signalName {payload}
                    const signalRegex = /SIGNAL:\s+(\w+)\s+(.*)$/s;
                    const match = responseText.match(signalRegex);
                    
                    let cleanResponseText = responseText;
                    let signalData: { name: string; payload: unknown } | null = null;

                    if (match) {
                        const signalName = match[1];
                        const signalPayloadStr = match[2];
                        let signalPayload: unknown = signalPayloadStr;
                        
                        try {
                            signalPayload = JSON.parse(signalPayloadStr);
                        } catch (_e) {
                            // If not valid JSON, keep it as a string
                        }

                        signalData = { name: signalName, payload: signalPayload };
                        cleanResponseText = responseText.replace(signalRegex, "").trim();
                    }

                    if (cleanResponseText) {
                        await callbacks.onResponse(cleanResponseText);
                    }

                    if (signalData) {
                        await callbacks.onSignal(signalData.name, signalData.payload);
                    }
                }
                await callbacks.onComplete();
                return;
            }

            throw new Error(`Unexpected response type: ${contentType}. Streaming is not supported.`);

        } catch (error) {
            callbacks.onError(error);
        }
    }
}
