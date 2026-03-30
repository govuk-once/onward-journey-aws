<script lang="ts">
  import type { ListableConversationMessageProps } from "$lib/types/ConversationMessage";
  import ConversationMessage from "$lib/components/ConversationMessage.svelte";
  import { untrack } from "svelte";

  interface Props {
    messages: ListableConversationMessageProps[]
    showTypingIndicator: boolean
    responderName?: string
  }

  let { messages, showTypingIndicator, responderName = "GOV.UK AI" }: Props = $props();
  let container: HTMLDivElement | undefined = $state();

  const scrollToBottom = (behavior: ScrollBehavior = 'smooth') => {
    if (container) {
      container.scrollTo({
        top: container.scrollHeight,
        behavior
      });
    }
  };

  $effect(() => {
    const _track = JSON.stringify(messages);
    const _typing = showTypingIndicator;

    untrack(() => {
      scrollToBottom('instant');
    });
  });
</script>

<div bind:this={container} class="app-conversation-layout__message-container">
  <ul id="app-conversation-layout__list">
    {#each messages as message (message.id)}
      <ConversationMessage {...message} />
    {/each}
    {#if showTypingIndicator}
      <ConversationMessage message="" isSelf={false} user={responderName} />
    {/if}
  </ul>
</div>
