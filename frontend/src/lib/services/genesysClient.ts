/**
 * Genesys Web Messaging Guest API Client
 *
 * A TypeScript client for connecting to the Genesys Cloud Web Messaging WebSocket API.
 * Reference: https://developer.genesys.cloud/api/digital/webmessaging/websocketapi
 */

import { v4 as uuid } from "uuid";

// ============================================================================
// Type Definitions
// ============================================================================

/** Base message type indicating whether this is a request or response */
export type BaseMessageType = "message" | "response";

/** The class/category of a message */
export type MessageDataType =
  | "string"
  | "SessionResponse"
  | "StructuredMessage"
  | "PresignedUrlResponse"
  | "AttachmentDeletedResponse"
  | "UploadFailureEvent"
  | "UploadSuccessEvent"
  | "ConnectionClosedEvent"
  | "LogoutEvent"
  | "SessionExpiredEvent"
  | "JwtResponse"
  | "GetConfigurationResponse"
  | "GenerateUrlError"
  | "TooManyRequestsErrorMessage"
  | "ResumeTokenResponse"
  | "SessionClearedEvent"
  | "Error"
  | "Unknown";

/** Direction of the message */
export type MessageDirection = "Inbound" | "Outbound";

/** Type of normalized message content */
export type NormalizedType = "Text" | "Structured" | "Receipt" | "Event";

/** Action types for outgoing requests */
export type RequestAction =
  | "configureSession"
  | "configureAuthenticatedSession"
  | "onMessage"
  | "getJwt"
  | "echo";

/** Base structure for all WebSocket messages */
export interface BaseMessage {
  type: BaseMessageType;
  class: MessageDataType;
  code: number;
  body: unknown;
  tracingId?: string;
}

/** Session response from configureSession */
export interface SessionResponse {
  tracingId?: string;
  connected: boolean;
  newSession?: boolean;
  readOnly?: boolean;
  clearedExistingSession?: boolean;
  allowedMedia?: AllowedMedia;
  blockedExtensions?: string[];
  maxCustomDataBytes?: number;
  durationSeconds?: number;
  expirationDate?: number;
  autoStarted?: boolean;
}

export interface AllowedMedia {
  inbound?: MediaType[];
  outbound?: MediaType[];
}

export interface MediaType {
  type: string;
  maxFileSizeKB?: number;
}

/** Channel information for messages */
export interface MessagingChannel {
  platform?: string;
  type?: string;
  messageId?: string;
  to?: ChannelEntity;
  from?: ChannelEntity;
  time?: string;
}

export interface ChannelEntity {
  nickname?: string;
  id?: string;
  idType?: string;
  firstName?: string;
  lastName?: string;
  image?: string;
  email?: string;
}

/** Content within a structured message */
export interface MessageContent {
  contentType: string;
  attachment?: AttachmentContent;
  quickReply?: QuickReplyContent;
  buttonResponse?: ButtonResponseContent;
  generic?: GenericContent;
  card?: CardContent;
  carousel?: CarouselContent;
}

export interface AttachmentContent {
  id?: string;
  mediaType?: string;
  url?: string;
  mime?: string;
  text?: string;
  sha256?: string;
  filename?: string;
}

export interface QuickReplyContent {
  text: string;
  payload: string;
  image?: string;
}

export interface ButtonResponseContent {
  text: string;
  payload: string;
}

export interface GenericContent {
  title?: string;
  description?: string;
  image?: string;
  actions?: ContentAction[];
}

export interface CardContent {
  title: string;
  description?: string;
  image?: string;
  actions?: ContentAction[];
}

export interface CarouselContent {
  cards: CardContent[];
}

export interface ContentAction {
  type: string;
  text: string;
  payload?: string;
  url?: string;
}

/** Event within a message */
export interface MessageEvent {
  eventType: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [key: string]: any;
}

/** Structured message received from the server */
export interface StructuredMessage {
  tracingId?: string;
  text?: string;
  type: NormalizedType;
  direction?: MessageDirection;
  id?: string;
  channel?: MessagingChannel;
  content?: MessageContent[];
  metadata?: Record<string, string>;
  events?: MessageEvent[];
  originatingEntity?: "Human" | "Bot";
}

/** Connection closed event */
export interface ConnectionClosedEvent {
  tracingId?: string;
  reason?: string;
}

/** Session expired event */
export interface SessionExpiredEvent {
  tracingId?: string;
}

/** JWT response */
export interface JwtResponse {
  tracingId?: string;
  jwt?: string;
  exp?: number;
}

// ============================================================================
// Request Types (Outgoing)
// ============================================================================

interface ConfigureSessionRequest {
  action: "configureSession";
  deploymentId: string;
  token: string;
  tracingId?: string;
}

interface SendMessageRequest {
  action: "onMessage";
  token: string;
  tracingId?: string;
  message: OutgoingMessage;
}

interface OutgoingMessage {
  type: NormalizedType;
  text?: string;
  content?: MessageContent[];
  channel?: {
    metadata?: Record<string, string>;
  };
}

// ============================================================================
// Event Listener Types
// ============================================================================

export interface GenesysClientEvents {
  connected: () => void;
  disconnected: (event: CloseEvent) => void;
  sessionResponse: (response: SessionResponse) => void;
  message: (message: StructuredMessage) => void;
  connectionClosed: (event: ConnectionClosedEvent) => void;
  sessionExpired: (event: SessionExpiredEvent) => void;
  error: (error: Error) => void;
  rawMessage: (message: BaseMessage) => void;
}

type EventCallback<K extends keyof GenesysClientEvents> =
  GenesysClientEvents[K];

// ============================================================================
// GenesysClient Class
// ============================================================================

export interface GenesysClientConfig {
  /** The WebSocket URL for the Genesys Web Messaging API */
  websocketUrl: string;
  /** The deployment key/ID for your Genesys deployment */
  deploymentKey: string;
}

export class GenesysClient {
  private websocketUrl: string;
  private deploymentKey: string;
  private socket: WebSocket | null = null;
  private token: string | null = null;
  private isSessionConfigured = false;
  private sessionResolve: ((response: SessionResponse) => void) | null = null;
  private listeners: Map<
    keyof GenesysClientEvents,
    Set<EventCallback<keyof GenesysClientEvents>>
  > = new Map();

  constructor(config: GenesysClientConfig) {
    this.websocketUrl = config.websocketUrl;
    this.deploymentKey = config.deploymentKey;
  }

  /**
   * Connect to the Genesys Web Messaging WebSocket
   */
  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.socket?.readyState === WebSocket.OPEN) {
        resolve();
        return;
      }

      const url = new URL(this.websocketUrl);
      url.searchParams.set("deploymentId", this.deploymentKey);

      this.socket = new WebSocket(url.toString());

      this.socket.onopen = () => {
        console.log("[GenesysClient] WebSocket connected");
        this.emit("connected");
        resolve();
      };

      this.socket.onclose = (event) => {
        console.log("[GenesysClient] WebSocket closed:", event.code, event.reason);
        this.isSessionConfigured = false;
        this.emit("disconnected", event);
      };

      this.socket.onerror = (error) => {
        console.error("[GenesysClient] WebSocket error:", error);
        const err = new Error("WebSocket connection error");
        this.emit("error", err);
        reject(err);
      };

      this.socket.onmessage = (event) => {
        this.handleMessage(event.data);
      };
    });
  }

  /**
   * Configure a new session with the Genesys server
   */
  configureSession(token?: string): Promise<SessionResponse> {
    return new Promise((resolve) => {
      this.token = token ?? this.generateToken();
      this.sessionResolve = resolve;

      console.log("[GenesysClient] Configuring session with token:", this.token);

      const request: ConfigureSessionRequest = {
        action: "configureSession",
        deploymentId: this.deploymentKey,
        token: this.token,
        tracingId: this.generateTracingId(),
      };

      this.send(request);
    });
  }

  /**
   * Send a text message to the conversation
   */
  sendMessage(text: string, metadata?: Record<string, string>): void {
    if (!this.token || !this.isSessionConfigured) {
      throw new Error("Session not configured. Call configureSession() first.");
    }

    console.log("[GenesysClient] Sending message:", text);

    const request: SendMessageRequest = {
      action: "onMessage",
      token: this.token,
      tracingId: this.generateTracingId(),
      message: {
        type: "Text",
        text,
        ...(metadata && {
          channel: { metadata },
        }),
      },
    };

    this.send(request);
  }

  /**
   * Disconnect from the WebSocket
   */
  disconnect(): void {
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.token = null;
    this.isSessionConfigured = false;
  }

  /**
   * Check if the client is connected
   */
  isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  /**
   * Check if the session is configured
   */
  isConfigured(): boolean {
    return this.isSessionConfigured;
  }

  /**
   * Get the current session token
   */
  getToken(): string | null {
    return this.token;
  }

  /**
   * Add an event listener
   */
  on<K extends keyof GenesysClientEvents>(
    event: K,
    callback: GenesysClientEvents[K],
  ): void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners
      .get(event)!
      .add(callback as EventCallback<keyof GenesysClientEvents>);
  }

  /**
   * Remove an event listener
   */
  off<K extends keyof GenesysClientEvents>(
    event: K,
    callback: GenesysClientEvents[K],
  ): void {
    this.listeners
      .get(event)
      ?.delete(callback as EventCallback<keyof GenesysClientEvents>);
  }

  // ============================================================================
  // Private Methods
  // ============================================================================

  private send(data: unknown): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket is not connected");
    }
    this.socket.send(JSON.stringify(data));
  }

  private handleMessage(data: string): void {
    try {
      const message = JSON.parse(data) as BaseMessage;
      console.log(`[GenesysClient] Raw message received: class=${message.class}, code=${message.code}`);
      this.emit("rawMessage", message);

      switch (message.class) {
        case "SessionResponse": {
          const body = message.body as SessionResponse;
          console.log("[GenesysClient] SessionResponse:", body);
          this.isSessionConfigured = body.connected;
          if (this.sessionResolve) {
            this.sessionResolve(body);
            this.sessionResolve = null;
          }
          this.emit("sessionResponse", body);
          break;
        }

        case "StructuredMessage":
          console.log("[GenesysClient] StructuredMessage:", (message.body as StructuredMessage).text);
          this.emit("message", message.body as StructuredMessage);
          break;

        case "ConnectionClosedEvent":
          console.log("[GenesysClient] ConnectionClosedEvent");
          this.emit("connectionClosed", message.body as ConnectionClosedEvent);
          break;

        case "SessionExpiredEvent":
          console.log("[GenesysClient] SessionExpiredEvent");
          this.emit("sessionExpired", message.body as SessionExpiredEvent);
          break;

        case "Error":
          console.error("[GenesysClient] Error class received:", message.body);
          this.emit("error", new Error(JSON.stringify(message.body)));
          break;
        
        default:
          console.log("[GenesysClient] Message class:", message.class);
      }
    } catch (e) {
      console.error("[GenesysClient] Handle message error:", e);
      this.emit("error", e instanceof Error ? e : new Error(String(e)));
    }
  }

  private emit<K extends keyof GenesysClientEvents>(
    event: K,
    ...args: Parameters<GenesysClientEvents[K]>
  ): void {
    const callbacks = this.listeners.get(event);
    if (callbacks) {
      callbacks.forEach((callback) => {
        (callback as (...args: Parameters<GenesysClientEvents[K]>) => void)(
          ...args,
        );
      });
    }
  }

  private generateToken(): string {
    return uuid();
  }

  private generateTracingId(): string {
    return this.generateToken();
  }
}
