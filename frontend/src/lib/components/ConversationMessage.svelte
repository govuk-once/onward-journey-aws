<script lang="ts">
  import type { ConversationMessageProps } from "$lib/types/ConversationMessage"
  let { message, user, image, isSelf = true }: ConversationMessageProps = $props();
</script>

<li class="app-c-conversation-message">
  <div class="app-c-conversation-message__message {isSelf ? 'app-c-conversation-message__message--user-message' : 'app-c-conversation-message__message--govuk-message'}">
    <div class="app-c-conversation-message__body {isSelf ? 'app-c-conversation-message__body--user-message' : 'app-c-conversation-message__body--govuk-message'}">
      {#if image || user}
      <div class="app-c-conversation-message__header">
        {#if image}
        <img class="app-c-conversation-message__image" src={image} alt="" />
        {/if}
        {#if user}
        <div class="app-c-conversation-message__identifier">
          <strong>{user}</strong>
        </div>
        {/if}
      </div>
      {/if}
      <div class="govuk-body-m govuk-!-margin-bottom-0">
        {#if message}
          {@html message}
        {:else}
          <span class="app-c-conversation-message__loading-text">
            {#if user === 'System'}
              Connecting you to a human advisor
            {:else if user === 'GOV.UK AI'}
              Thinking
            {:else}
              Typing
            {/if}
            <span class="app-c-conversation-message__loading-ellipsis" aria-hidden="true">...</span>
          </span>
        {/if}
      </div>
    </div>
  </div>
</li>
