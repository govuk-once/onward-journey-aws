<script lang="ts">
  import type { ListableConversationMessageProps } from "$lib/types/ConversationMessage";
  import ConversationMessageContainer from "$lib/components/ConversationMessageContainer.svelte";
  import QuestionForm from "$lib/components/QuestionForm.svelte";
  import type { SendMessageHandler } from "$lib/components/QuestionForm.svelte";
  import { v7 as uuid } from "uuid";
  import { GenesysClient } from "$lib/services/genesysClient.js";
  import { OrchestratorClient } from "$lib/services/orchestratorClient";
  import { markdownToHtml } from "$lib/utils/markdown";
  import { onMount } from "svelte";
  import { env } from "$env/dynamic/public";

  let connectionType: 'AI' | 'HUMAN' = $state('AI');
  let isConnecting: boolean = $state(false);
  let messages: ListableConversationMessageProps[] = $state([]);
  let showTypingIndicator: boolean = $state(false);
  let responderName: string = $state("GOV.UK AI");
  let threadId = uuid();
  let genesysClient: GenesysClient | null = null;
  let orchestrator: OrchestratorClient | null = null;

  const CONNECTING_MESSAGE_ID = "connecting-to-human-advisor";

  const orchestratorUrl = env.PUBLIC_ORCHESTRATOR_URL;
  if (!orchestratorUrl) {
    console.warn("PUBLIC_ORCHESTRATOR_URL not set. AI Chat will not be available.");
  } else {
    orchestrator = new OrchestratorClient(orchestratorUrl);
  }

  const handleAiMessage = async (userInput: string) => {
    if (connectionType !== 'AI' || !orchestrator) return;

    // Add user message to UI
    messages.push({
      message: await markdownToHtml(userInput),
      isSelf: true,
      id: uuid()
    });

    responderName = "GOV.UK AI";
    showTypingIndicator = true;

    await orchestrator.sendMessage(userInput, threadId, {
      onResponse: async (response) => {
        console.log("AI response:", response);
        // Only show AI responses if we haven't switched to HUMAN mode
        if (connectionType === 'AI') {
          showTypingIndicator = false;
          messages.push({
            message: await markdownToHtml(response),
            user: "GOV.UK AI",
            isSelf: false,
            id: uuid()
          });
        }
      },
      onSignal: async (signal, payload) => {
        if (signal === "initiate_live_handoff") {
          await switchToHuman(payload);
        }
      },
      onComplete: () => {
        if (connectionType === 'AI') {
          showTypingIndicator = false;
        }
      },
      onError: (err) => {
        console.error("Orchestrator error:", err);
        showTypingIndicator = false;
      }
    });
  };

  interface HandoffPayload {
    websocketUrl?: string;
    deploymentId?: string;
    region?: string;
    token?: string;
  }

  const switchToHuman = async (payload: unknown) => {
    if (connectionType === 'HUMAN') return;

    console.log("Switching to HUMAN mode", payload);
    isConnecting = true;
    connectionType = 'HUMAN';

    // Add a system message
    messages.push({
      message: '', // keep blank to get default message
      user: "System",
      isSelf: false,
      id: CONNECTING_MESSAGE_ID
    });

    const handoff = payload as HandoffPayload;
    const websocketUrl = handoff.websocketUrl;
    const deploymentKey = handoff.deploymentId;

    console.log("Genesys Config:", { websocketUrl, deploymentKey, hasToken: !!handoff.token });

    if (!websocketUrl || !deploymentKey) {
        console.error("Missing Genesys configuration");
        messages = messages.filter(m => m.id !== CONNECTING_MESSAGE_ID);
        messages.push({
            message: "Error: Genesys configuration missing. Cannot connect to human advisor.",
            user: "System",
            isSelf: false,
            id: uuid()
        });
        isConnecting = false;
        return;
    }

    genesysClient = new GenesysClient({ websocketUrl, deploymentKey });

    genesysClient.on("message", async (msg) => {
      console.log("Genesys message event:", {
        id: msg.id,
        direction: msg.direction,
        type: msg.type,
        text: msg.text,
        hasEvents: !!msg.events?.length
      });

      if (msg.text) {
        if (msg.direction === "Inbound") {
          console.log("Ignoring inbound message (echo)");
          return;
        }

        console.log("Processing outbound message from advisor:", msg.text);

        const newMessage: ListableConversationMessageProps = {
          message: await markdownToHtml(msg.text),
          user: msg.channel?.from?.nickname ?? "Advisor",
          image: msg.channel?.from?.image ?? undefined,
          isSelf: false,
          id: msg.id ?? uuid()
        };

        messages = [
          ...messages,
          newMessage
        ];

        showTypingIndicator = false;
      } else if (msg.type == "Event" && msg.events?.find((e) => e.eventType == "Typing")) {
        console.log("Typing event received");
        responderName = msg.channel?.from?.nickname ?? "Advisor";
        showTypingIndicator = true;
        const typingEvent = msg.events?.find((e) => e.eventType == "Typing");
        if (typingEvent?.typing?.duration) {
          setTimeout(() => { showTypingIndicator = false; }, typingEvent.typing.duration);
        }
      }
    });

    try {
      console.log("Attempting to connect to Genesys WebSocket...");
      await genesysClient.connect();
      console.log("WebSocket connected. Configuring session...");

      const sessionResponse = await genesysClient.configureSession(handoff.token);
      console.log("Session configured:", sessionResponse);

      if (!sessionResponse.connected) {
        throw new Error("Session response indicated not connected");
      }

      console.log("Sending initial context message to advisor...");
      genesysClient.sendMessage("User transferred from AI. Thread ID: " + threadId);
      console.log("Initial message sent.");

      messages = messages.map(m =>
        m.id === CONNECTING_MESSAGE_ID
        ? { ...m, message: "Connected to a human advisor." }
        : m
      );

      isConnecting = false;
      showTypingIndicator = false;
    } catch (err) {
      console.error("Failed to connect to Genesys:", err);
      messages = messages.filter(m => m.id !== CONNECTING_MESSAGE_ID);
      messages.push({
        message: `Error: Failed to connect to a human advisor (${err instanceof Error ? err.message : 'Unknown error'}).`,
        user: "System",
        isSelf: false,
        id: uuid()
      });
      isConnecting = false;
    }
  };

  const handleHumanMessage = async (userInput: string) => {
    if (isConnecting) return;

    messages.push({
      message: await markdownToHtml(userInput),
      isSelf: true,
      id: uuid()
    });
    genesysClient?.sendMessage(userInput);
  };

  let sendMessageHandler: SendMessageHandler = $state((message) => {
    if (connectionType === 'AI') {
      handleAiMessage(message);
    } else {
      handleHumanMessage(message);
    }
  });

  onMount(() => {
    (async () => {
      messages.push({
        message: await markdownToHtml("Hello! I'm the GOV.UK Onward Journey AI. How can I help you today?"),
        user: "GOV.UK AI",
        isSelf: false,
        id: uuid()
      });
    })();

    return () => {
      genesysClient?.disconnect();
    };
  });
</script>

<main class="app-conversation-layout__main" id="main-content">
  <div class="app-conversation-layout__wrapper app-conversation-layout__width-restrictor">
    <ConversationMessageContainer messages={messages} showTypingIndicator={showTypingIndicator} responderName={responderName}/>
    <QuestionForm messageHandler={sendMessageHandler} disabled={isConnecting}/>
  </div>
</main>
