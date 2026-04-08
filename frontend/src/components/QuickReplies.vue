<template>
  <div v-if="replies.length > 0" class="quick-replies">
    <div class="quick-replies-title">AI 推荐回复</div>
    <div
      v-for="(reply, i) in replies"
      :key="i"
      class="quick-reply-card"
      @click="$emit('select', reply)"
    >
      <span class="quick-reply-text">{{ reply }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  replies: string[]
}>()

defineEmits<{
  select: [reply: string]
}>()
</script>
