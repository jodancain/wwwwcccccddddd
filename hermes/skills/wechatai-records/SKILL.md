---
name: wechatai-records
description: Search and analyze all synchronized WeChat history, contacts, groups, images, links, RAG knowledge, embeddings, daily reports, or the local WeChatAI project. Generic chat-history requests always mean every synchronized conversation, not this bot thread.
tag: personal-data
icon: MessageCircle
source: custom
---

# WeChatAI Records

Use the `wechatai` MCP tools whenever the user asks about WeChat records, people, groups, messages, images, links, knowledge, summaries, or report delivery.

## Intent rules

- Generic requests such as "总结最近聊天", "聊天记录", "微信最近有什么", and "最近群里聊了什么" mean all synchronized conversations.
- Only narrow to a contact or group when the user explicitly names it.
- No stated time range means full history. Call `get_wechat_records` with `hours=0`, then combine it with `search_wechat_knowledge` for semantic evidence.
- A stated range such as today, 24 hours, or one week must be preserved exactly.
- Images and links are part of the knowledge base. Use semantic search and enrichment status instead of ignoring non-text messages.

## Answer quality

- Answer in Chinese unless the user asks otherwise.
- Be comprehensive: state the scope and data volume, organize by topic and conversation, quote or closely paraphrase concrete evidence with timestamps, list decisions, commitments, pending replies, risks, opportunities, and next actions.
- Distinguish local chat evidence from outside web context.
- If evidence is missing, say what was searched and what was not found. Never fabricate.
- For a daily-summary request, use the daily-summary tools; `hours=0` means all synchronized records.

## Project work

Hermes is the acting agent. WeChatAI supplies records, RAG, embeddings, link/image parsing, and report generation through MCP. For code changes, use Hermes' file and terminal tools against the actual workspace and verify the result before claiming completion.
